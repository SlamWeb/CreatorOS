import os
import tempfile
from pathlib import Path

from creatoros.config import PROJECT_ROOT
from creatoros.tools.builtins import MAX_READ_BYTES, read_file


def main():
    assert read_file("SPEC.md").is_error is False

    protected = read_file(".env")
    assert protected.error_type == "sensitive_path"

    handle, raw_path = tempfile.mkstemp(prefix="creatoros-smoke-", suffix=".txt", dir=PROJECT_ROOT)
    temp_path = Path(raw_path)
    try:
        os.close(handle)
        temp_path.write_bytes(b"x" * (MAX_READ_BYTES + 1))
        oversized = read_file(temp_path.name)
        assert oversized.error_type == "file_too_large"
    finally:
        temp_path.unlink(missing_ok=True)

    print("read_file_guardrail_smoke=passed")


if __name__ == "__main__":
    main()
