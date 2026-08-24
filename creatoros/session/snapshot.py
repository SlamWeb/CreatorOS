import json

from ..config import SESSION_FILE, SYSTEM_PROMPT


def new_messages():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def load_messages():
    if not SESSION_FILE.exists():
        return new_messages()

    try:
        messages = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if not isinstance(messages, list) or not all(
            isinstance(message, dict) for message in messages
        ):
            raise ValueError("会话文件必须是消息对象列表。")
        if not messages or messages[0].get("role") != "system":
            messages.insert(0, new_messages()[0])
        return messages
    except (OSError, ValueError, json.JSONDecodeError):
        print("[Session] 会话文件无效，将从新会话开始。")
        return new_messages()


def save_messages(messages):
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = SESSION_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_file.replace(SESSION_FILE)


def find_tool_result(messages, result_ref: str) -> dict | None:
    for message in reversed(list(messages)):
        if (
            message.get("role") == "tool"
            and message.get("tool_call_id") == result_ref
        ):
            return message
    return None
