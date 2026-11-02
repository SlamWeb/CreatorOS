import json

from ..config import SESSION_FILE, SYSTEM_PROMPT


def new_messages():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def load_messages(session_file=None, *, require_existing=False):
    path = session_file or SESSION_FILE
    try:
        try:
            contents = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if require_existing:
                raise
            return new_messages()
        # An unreadable or malformed existing file is not an empty conversation.
        messages = json.loads(contents)
        if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
            raise ValueError("会话文件必须是消息对象列表。")
        if not messages or messages[0].get("role") != "system":
            messages.insert(0, new_messages()[0])
        return messages
    except (OSError, ValueError) as error:
        # Keep the original exception while allowing the Web host to avoid
        # automatically repairing a ledger that it just failed to read.
        error.creatoros_history_read_error = True
        raise


def save_messages(messages, session_file=None):
    # Lazy import keeps session initialization independent of integration adapters.
    from ..integrations.atomic_file import atomic_write_text

    path = session_file or SESSION_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(messages, ensure_ascii=False, indent=2))


def find_tool_result(messages, result_ref: str) -> dict | None:
    for message in reversed(list(messages)):
        if (
            message.get("role") == "tool"
            and message.get("tool_call_id") == result_ref
        ):
            return message
    return None
