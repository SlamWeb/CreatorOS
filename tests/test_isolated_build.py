"""Parallel Vite builds must not overwrite another build's dist."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parent.parent


def files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class IsolatedBuildTest(unittest.TestCase):
    def test_e2e_entry_builds_in_its_own_temporary_directory(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-e2e-entry-proof-") as temporary:
            existing = Path(temporary) / "existing-build"
            existing.mkdir()
            (existing / "keep.txt").write_text("keep", encoding="utf-8")
            env = os.environ.copy()
            env.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary, CREATOROS_E2E_PORT="18991",
                       CREATOROS_BUILD_OUT_DIR=str(existing))
            result = subprocess.run(["node", "e2e.mjs", "e2e/home-page.spec.ts", "--list",
                                     "--grep", "never requests accounts"], cwd=REPO / "web", env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", errors="replace"))
            builds = list(Path(temporary).glob("creatoros-e2e-cli-*/dist/index.html"))
            self.assertEqual(len(builds), 1)
            self.assertEqual((existing / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_parallel_builds_are_independent_and_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="creatoros-build-proof-") as temporary:
            parent = Path(temporary)
            processes = []
            logs = []
            try:
                for label in ("a", "b"):
                    env = os.environ.copy()
                    env["CREATOROS_BUILD_OUT_DIR"] = str(parent / label)
                    log = (parent / f"{label}.log").open("w", encoding="utf-8")
                    logs.append(log)
                    processes.append(subprocess.Popen(["node", "build.mjs"], cwd=REPO / "web", env=env,
                                                      stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT))
                for process in processes:
                    self.assertEqual(process.wait(timeout=240), 0)
                first, second = files(parent / "a"), files(parent / "b")
                self.assertIn("index.html", first)
                self.assertTrue(any(name.startswith("assets/") and name.endswith(".js") for name in first))
                self.assertEqual(first, second)
                env = os.environ.copy()
                env["CREATOROS_BUILD_OUT_DIR"] = str(parent / "a")
                rejected = subprocess.run(["node", "build.mjs"], cwd=REPO / "web", env=env,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          timeout=30)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(first, files(parent / "a"))
            except Exception:
                for log in logs:
                    log.flush()
                for name in ("a", "b"):
                    path = parent / f"{name}.log"
                    if path.is_file():
                        print(f"\n--- {name} ---\n{path.read_text(encoding='utf-8', errors='replace')}")
                raise
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=20)
                for log in logs:
                    log.close()


if __name__ == "__main__":
    unittest.main()
