from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.configure_personclone_auth import _upsert_env_value


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / ".env"
        path.write_text("DEEPSEEK_API_KEY=keep\nPERSONCLONE_SESSION_COOKIE=old\n", encoding="utf-8")
        _upsert_env_value(path, "PERSONCLONE_SESSION_COOKIE", "new-cookie")
        text = path.read_text(encoding="utf-8")
        assert "DEEPSEEK_API_KEY=keep" in text
        assert "PERSONCLONE_SESSION_COOKIE=new-cookie" in text
        assert "old" not in text

    print("personclone_auth_helper_smoke=passed")


if __name__ == "__main__":
    main()
