from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from PIL import Image

from creatoros.integrations.skill_task_runner import SkillTaskRunner, SkillTaskRunnerError


_WASI_AVAILABLE = bool(
    os.environ.get("CREATOROS_SKILL_TASK_WASM_ROOT")
    and (os.environ.get("CREATOROS_SKILL_TASK_WASI_PATH") or importlib.util.find_spec("wasmtime"))
)


class SkillTaskRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="creatoros-runner-test-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.workspace = self.parent / "workspace"
        for name in ("skills", "inputs", "work", "out"):
            (self.workspace / name).mkdir(parents=True, exist_ok=True)

    def script(self, source: str, *, name: str = "skills/probe.py") -> str:
        (self.workspace / name).write_text(textwrap.dedent(source), encoding="utf-8")
        return name

    def test_invalid_script_and_external_alias_are_rejected(self):
        (self.parent / "outside.py").write_text("raise SystemExit(99)", encoding="utf-8")
        for filename in ("../outside.py", "skills/../out/probe.py", "C:/outside.py", "skills/evil.py:secret.py",
                         "skills/tool.sh", "inputs/entry.py"):
            with self.subTest(filename=filename), self.assertRaises(SkillTaskRunnerError) as caught:
                SkillTaskRunner().run(self.workspace, filename)
            self.assertEqual(caught.exception.code, "script_invalid")
        os.link(self.parent / "outside.py", self.workspace / "work" / "linked.py")
        with self.assertRaisesRegex(SkillTaskRunnerError, "links and special files"):
            SkillTaskRunner().run(self.workspace, "work/linked.py")

    def test_unavailable_sandbox_never_falls_back_to_host_execution(self):
        script = self.script("from pathlib import Path; Path('out/uncontained').touch()")
        with patch.dict(os.environ, CREATOROS_SKILL_TASK_PYTHON=str(self.parent / "missing-python")):
            with self.assertRaises(SkillTaskRunnerError) as caught:
                SkillTaskRunner().run(self.workspace, script)
        self.assertEqual(caught.exception.code, "script_runner_unavailable")
        self.assertFalse((self.workspace / "out" / "uncontained").exists())

    @unittest.skipUnless(_WASI_AVAILABLE, "Optional WASI runtime is unavailable")
    def test_real_python_execution_and_filesystem_network_process_boundaries(self):
        input_text = self.workspace / "inputs" / "request.txt"
        input_text.write_text("authorized", encoding="utf-8")
        sibling_task = self.parent / "other-task"
        sibling_task.mkdir()
        sibling_data = sibling_task / "other.txt"
        sibling_data.write_text("untouched", encoding="utf-8")
        host_file = self.parent / "host-secret.txt"
        host_file.write_text("hidden", encoding="utf-8")
        self.script("WIDTH = 23", name="skills/helper.py")
        script = self.script("""
            from pathlib import Path
            from helper import WIDTH
            import json
            import os
            import png
            import sys

            def denial(action):
                try:
                    action()
                except (ImportError, OSError) as error:
                    return type(error).__name__
                return None

            def connect(host, port):
                import socket
                with socket.socket() as client:
                    client.settimeout(0.5)
                    client.connect((host, port))

            def child_process():
                import subprocess
                return subprocess.run([sys.executable, "-c", "print('unexpected')"])

            host_file, sibling_file, port, sample = sys.argv[1:]
            assert sys.stdin.read() == ""
            assert Path("inputs/request.txt").read_text() == "authorized"
            assert "CREATOROS_TEST_HOST_SECRET" not in os.environ
            result = {
                "host_read": denial(lambda: Path(host_file).read_bytes()),
                "host_traversal": denial(lambda: Path("work/../../host-secret.txt").read_bytes()),
                "outside_write": denial(lambda: Path(host_file).write_text("changed")),
                "sibling_read": denial(lambda: Path(sibling_file).read_bytes()),
                "sibling_traversal": denial(lambda: Path("out/../../other-task/other.txt").read_bytes()),
                "input_write": denial(lambda: Path("inputs/request.txt").write_text("changed")),
                "skill_write": denial(lambda: Path("skills/helper.py").write_text("changed")),
                "workspace_write": denial(lambda: Path("outside").touch()),
                "output_root_deletion": denial(lambda: Path("out").rmdir()),
                "child_process": denial(child_process),
                "loopback": denial(lambda: connect("127.0.0.1", int(port))),
                "external_network": denial(lambda: connect("192.0.2.1", 80)),
            }
            Path("work/intermediate.txt").write_text(sample, encoding="utf-8")
            with Path("out/sample.png").open("wb") as output:
                png.Writer(WIDTH, 17, greyscale=False).write(output, [[0, 128, 0] * WIDTH] * 17)
            print(json.dumps(result), flush=True)
        """)
        with socket.socket() as listener, patch.dict(os.environ, CREATOROS_TEST_HOST_SECRET="hidden"):
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            sample = "带空格的文本 / quoted \"input\""
            port = str(listener.getsockname()[1])
            receipt = SkillTaskRunner().run(self.workspace, script, [str(host_file), str(sibling_data), port, sample])
        self.assertEqual(receipt.returncode, 0, asdict(receipt))
        self.assertEqual(receipt.stderr, "")
        denied = json.loads(receipt.stdout)
        self.assertTrue(all(error is not None for error in denied.values()), denied)
        self.assertEqual(input_text.read_text(encoding="utf-8"), "authorized")
        self.assertEqual(host_file.read_text(encoding="utf-8"), "hidden")
        self.assertEqual(sibling_data.read_text(encoding="utf-8"), "untouched")
        self.assertEqual((self.workspace / "work" / "intermediate.txt").read_text(encoding="utf-8"), sample)
        with Image.open(self.workspace / "out" / "sample.png") as image:
            self.assertEqual(image.size, (23, 17))
            self.assertEqual(image.getpixel((0, 0)), (0, 128, 0))

    @unittest.skipUnless(_WASI_AVAILABLE, "Optional WASI runtime is unavailable")
    def test_timeout_and_output_limit_terminate_the_process(self):
        timeout_script = self.script("while True: pass", name="work/forever.py")
        flood_script = self.script("""
            import sys
            while True:
                sys.stdout.write("a" * 4096)
                sys.stderr.write("b" * 4096)
        """, name="work/flood.py")
        cases = ((timeout_script, "script_timeout"), (flood_script, "script_output_limit"))
        for script, code in cases:
            with self.subTest(script=script), self.assertRaises(SkillTaskRunnerError) as caught:
                SkillTaskRunner(timeout=2, output_limit=8192).run(self.workspace, script)
            self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
