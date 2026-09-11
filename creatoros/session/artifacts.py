"""Local evidence files; paths are host generated, never model selected."""
import hashlib
import json
from pathlib import Path


def root_for(session_file):
    path = Path(session_file).absolute()
    root = path.with_suffix(".tool-results")
    if root.is_symlink():
        raise ValueError("Tool archive root cannot be a symlink")
    return root


def _write(path, text):
    if path.is_symlink():
        raise ValueError("Tool archive file cannot be a symlink")
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.is_symlink():
        raise ValueError("Tool archive temporary file cannot be a symlink")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
    temporary.replace(path)


def externalize(messages, session_file):
    """Return a copied view; full tool text and index are persisted separately."""
    from copy import deepcopy
    root = root_for(session_file)
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "index.json"
    if index_path.is_symlink():
        raise ValueError("Tool archive index cannot be a symlink")
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    names = {}
    projected = deepcopy(list(messages))
    for message in projected:
        for call in message.get("tool_calls", []):
            names[call["id"]] = call.get("name", "tool")
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        ref = message.get("tool_call_id", "unknown")
        digest = hashlib.sha256((ref + "\0" + content).encode()).hexdigest()
        path = root / (digest + ".txt")
        if not path.exists():
            _write(path, content)
        if path.is_symlink():
            raise ValueError("Tool archive file cannot be a symlink")
        facts = {}
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                for key in ("status", "error", "run_id", "operation_id", "id", "series_id", "stale", "total"):
                    value = data.get(key)
                    if isinstance(value, (str, int, bool)):
                        facts[key] = value[:180] if isinstance(value, str) else value
                for key in ("items", "candidates", "topics"):
                    if isinstance(data.get(key), list):
                        facts[key + "_count"] = len(data[key])
        except ValueError:
            pass
        description = f"{names.get(ref, 'tool')} 返回记录，{len(content)} 字符；历史字段={json.dumps(facts, ensure_ascii=False)}"
        if content.startswith("[tool_error"):
            description += "；工具报告错误"
        if names.get(ref) or digest not in index:
            index[digest] = {"result_ref": ref, "description": description, "path": path.name}
        else:
            description = index[digest]["description"]
        message["content"] = f"[external tool result] {description}\nresult_ref={ref}\n用 read_file 读取原文：{path.resolve().as_posix()}（unit=chars，可分页）"
    _write(index_path, json.dumps(index, ensure_ascii=False, indent=2))
    return projected


def index_note(session_file):
    return "\n\n历史工具原文索引（按需 read_file，不必全部读取）：" + (root_for(session_file) / "index.json").resolve().as_posix()
