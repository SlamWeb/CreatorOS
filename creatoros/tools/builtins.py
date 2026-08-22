from datetime import datetime

from ..config import PROJECT_ROOT


def get_current_time():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def get_current_date():
    return datetime.now().date().isoformat()


def read_file(path, offset=1, limit=None):
    if offset < 1:
        return "错误：offset 必须从 1 开始。"

    if limit is not None and limit < 1:
        return "错误：limit 必须大于 0。"

    requested_path = (PROJECT_ROOT / path).resolve()

    try:
        requested_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return "错误：只能读取 CreatorOS 项目目录内的文件。"

    try:
        lines = requested_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return ""

        start_index = offset - 1
        if start_index >= len(lines):
            return f"错误：offset {offset} 超出文件范围（共 {len(lines)} 行）。"

        end_index = start_index + limit if limit is not None else len(lines)
        result = "\n".join(lines[start_index:end_index])

        if end_index < len(lines):
            remaining = len(lines) - end_index
            next_offset = end_index + 1
            result += f"\n\n[文件还有 {remaining} 行，可使用 offset={next_offset} 继续读取。]"

        return result
    except FileNotFoundError:
        return f"文件不存在：{path}"
    except IsADirectoryError:
        return f"这不是文件：{path}"
    except UnicodeDecodeError:
        return f"文件不是 UTF-8 文本：{path}"


def write_file(path, content):
    requested_path = (PROJECT_ROOT / path).resolve()
    try:
        requested_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return "错误：只能写入 CreatorOS 项目目录内的文件。"

    if requested_path.exists():
        return f"错误：文件已存在，为避免覆盖：{path}"

    try:
        requested_path.write_text(content, encoding="utf-8")
        return f"已写入文件：{path}"
    except FileNotFoundError:
        return f"错误：父目录不存在：{path}"
    except OSError as error:
        return f"写入文件失败：{error}"
