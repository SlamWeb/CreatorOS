from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = "你是 CreatorOS 的 Agent，只在必要时调用已提供的工具。"
SESSION_FILE = PROJECT_ROOT / "sessions" / "latest.json"
