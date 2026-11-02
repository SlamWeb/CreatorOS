"""Run normal unittest arguments while retaining machine-readable coverage."""
import argparse
import json
from pathlib import Path
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True)
    args, remaining = parser.parse_known_args()
    target = Path(args.result)

    class RecordingRunner(unittest.TextTestRunner):
        def run(self, suite):
            discovered = suite.countTestCases()
            result = super().run(suite)
            receipt = {"discovered": discovered, "run": result.testsRun, "skipped": len(result.skipped),
                       "skip_reasons": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
                       "failures": len(result.failures), "errors": len(result.errors),
                       "expected_failures": len(result.expectedFailures),
                       "unexpected_successes": len(result.unexpectedSuccesses)}
            target.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            return result

    program = unittest.TestProgram(module=None, argv=["unittest", *remaining], exit=False, testRunner=RecordingRunner)
    return 0 if program.result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
