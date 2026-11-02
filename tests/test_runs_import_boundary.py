"""The read-only production interface should not load the Codex executor."""

import json
import subprocess
import sys
import unittest


class RunImportBoundaryTests(unittest.TestCase):
    def test_observation_import_does_not_load_codex_or_run_service(self):
        code = ("import json,sys; import creatoros.runs.observation; "
                "print(json.dumps({'codex': 'creatoros.integrations.codex' in sys.modules, "
                "'service': 'creatoros.runs.service' in sys.modules}))")
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                encoding='utf-8', check=True, timeout=45)
        self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]), {'codex': False, 'service': False})

    def test_run_error_is_the_same_class_for_old_and_new_imports(self):
        from creatoros.runs import ContentRunError as package_error
        from creatoros.runs.errors import ContentRunError as domain_error
        from creatoros.runs.service import ContentRunError as service_error
        self.assertIs(package_error, domain_error)
        self.assertIs(service_error, domain_error)

    def test_public_imports_still_resolve_from_a_fresh_interpreter(self):
        code = ("import creatoros.runs as runs; "
                "scope={}; exec('from creatoros.runs import *', scope); "
                "assert all(scope[name] is getattr(runs, name) for name in runs.__all__); "
                "assert not hasattr(runs, 'not_a_public_run_symbol')")
        subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                       encoding='utf-8', check=True, timeout=90)


if __name__ == '__main__':
    unittest.main()
