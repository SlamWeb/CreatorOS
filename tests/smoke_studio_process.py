"""No model calls: exercise real OS children, process-tree cleanup and crash journal."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Timer
from time import monotonic, sleep

import psutil

from creatoros.integrations.codex import CodexProducer, CodexProducerError
from creatoros.integrations.process_tree import ProcessTree
from creatoros.runs.ownership import ExecutionOwnershipError, LocalExecutionGuard
from creatoros.storage import Database


CHILD = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"


def gone(pid):
    try:
        return not psutil.Process(pid).is_running() or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def wait_gone(*pids):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        if all(gone(pid) for pid in pids):
            return
        sleep(0.02)
    raise AssertionError(f"Owned child survived: {pids}")


if len(sys.argv) > 1 and sys.argv[1] == "--crash-host":
    guard = LocalExecutionGuard(Database(sys.argv[2]))
    guard.__enter__()
    guard.begin(owner_id="crash-probe", run_id="probe", attempt_id="probe")
    tree = ProcessTree()
    child = tree.start([sys.executable, "-u", "-c", CHILD], stdout=subprocess.PIPE, text=True,
                       on_started=lambda identity: guard.process_started("crash-probe", identity))
    grandchild = int(child.stdout.readline())
    print(json.dumps([child.pid, grandchild]), flush=True)
    os._exit(0)  # Intentionally skip Python cleanup; the OS closes the Job handle.


class LocalProducer(CodexProducer):
    script = "import time; time.sleep(60)"

    def _command(self, *_args):
        return [sys.executable, "-u", "-c", self.script]


with TemporaryDirectory() as temporary:
    root = Path(temporary)
    tree = ProcessTree()
    identities = []
    child = tree.start([sys.executable, "-u", "-c", CHILD], stdout=subprocess.PIPE, text=True,
                       on_started=identities.append)
    grandchild = int(child.stdout.readline())
    assert identities[0]["created_at"] > 0
    tree.close()
    child.stdout.close()
    wait_gone(child.pid, grandchild)

    producer = LocalProducer(project_root=root, generated_images_root=root, timeout_seconds=0.2)
    try:
        producer._execute("probe", root)
    except CodexProducerError as error:
        assert error.error_type == "codex_timeout"
    else:
        raise AssertionError("timeout must terminate the owned tree")
    cancel = Event()
    producer.timeout_seconds = 60
    timer = Timer(0.2, cancel.set)
    timer.start()
    try:
        producer._execute("probe", root, cancel_event=cancel)
    except CodexProducerError as error:
        assert error.error_type == "codex_interrupted"
    else:
        raise AssertionError("shutdown must close the stream")
    finally:
        timer.cancel()

    producer.script = "import sys; sys.exit(7)"
    try:
        producer._execute("probe", root)
    except CodexProducerError as error:
        assert error.error_type == "codex_exec_failed"
    else:
        raise AssertionError("nonzero subprocess exit must be reported")

    if os.name == "nt":
        url = f"sqlite:///{(root / 'crash.db').as_posix()}"
        crashed = subprocess.run([sys.executable, "-m", "tests.smoke_studio_process", "--crash-host", url],
                                 capture_output=True, text=True, timeout=15)
        assert crashed.returncode == 0, crashed.stderr
        wait_gone(*json.loads(crashed.stdout))
        guard = LocalExecutionGuard(Database(url))
        with guard:
            record = json.loads(guard.journal.read_text(encoding="utf-8"))
            assert record["phase"] == "process_running" and record["process"]["created_at"] > 0
            try:
                guard.assert_clean()
            except ExecutionOwnershipError:
                pass
            else:
                raise AssertionError("unclean ownership must be reconciled before retry")

print("studio_process_smoke=passed tree=terminated timeout=terminated shutdown=terminated exit=reported hard_crash=blocked")
