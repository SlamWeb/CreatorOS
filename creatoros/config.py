from pathlib import Path
import os

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = "你是 CreatorOS 的 Agent，只在必要时调用已提供的工具。"
SESSION_FILE = PROJECT_ROOT / "sessions" / "latest.json"

PERSONCLONE_BASE_URL = os.getenv("PERSONCLONE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PERSONCLONE_SESSION_COOKIE = os.getenv("PERSONCLONE_SESSION_COOKIE", "")

try:
    PERSONCLONE_TIMEOUT_SECONDS = float(os.getenv("PERSONCLONE_TIMEOUT_SECONDS", "180"))
except ValueError:
    PERSONCLONE_TIMEOUT_SECONDS = 180.0

ZHIHU_OPENAPI_BASE_URL = os.getenv(
    "ZHIHU_OPENAPI_BASE_URL",
    "https://developer.zhihu.com",
).rstrip("/")
ZHIHU_ACCESS_SECRET = os.getenv("ZHIHU_ACCESS_SECRET", "").strip()

try:
    ZHIHU_TIMEOUT_SECONDS = float(os.getenv("ZHIHU_TIMEOUT_SECONDS", "30"))
except ValueError:
    ZHIHU_TIMEOUT_SECONDS = 30.0

try:
    CODEX_PRODUCER_TIMEOUT_SECONDS = float(os.getenv("CODEX_PRODUCER_TIMEOUT_SECONDS", "1800"))
except ValueError:
    CODEX_PRODUCER_TIMEOUT_SECONDS = 1800.0
