from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT
from ..context import RuntimeContext
from .results import ToolResult

MAX_READ_BYTES = 128 * 1024
SENSITIVE_DIRECTORY_NAMES = {".git", "sessions"}


def _project_root(context: RuntimeContext | None) -> Path:
    return (context.project_root if context is not None else PROJECT_ROOT).resolve()


def _is_sensitive_path(requested_path, project_root):
    relative_parts = requested_path.relative_to(project_root).parts
    if not relative_parts:
        return False

    normalized_parts = [part.casefold() for part in relative_parts]
    if any(part in SENSITIVE_DIRECTORY_NAMES for part in normalized_parts[:-1]):
        return True

    filename = normalized_parts[-1]
    return filename == ".env" or filename.startswith(".env.") or filename.endswith(
        (".pem", ".key")
    )


def get_current_time(context: RuntimeContext | None = None) -> ToolResult:
    return ToolResult(content=datetime.now().astimezone().isoformat(timespec="seconds"))


def get_current_date(context: RuntimeContext | None = None) -> ToolResult:
    return ToolResult(content=datetime.now().date().isoformat())


def read_file(path, offset=1, limit=None, context: RuntimeContext | None = None) -> ToolResult:
    if offset < 1:
        return ToolResult(
            content="错误：offset 必须从 1 开始。",
            is_error=True,
            error_type="invalid_arguments",
            retryable=True,
        )

    if limit is not None and limit < 1:
        return ToolResult(
            content="错误：limit 必须大于 0。",
            is_error=True,
            error_type="invalid_arguments",
            retryable=True,
        )

    project_root = _project_root(context)
    requested_path = (project_root / path).resolve()

    try:
        requested_path.relative_to(project_root)
    except ValueError:
        return ToolResult(
            content="错误：只能读取 CreatorOS 项目目录内的文件。",
            is_error=True,
            error_type="path_out_of_scope",
            retryable=True,
        )

    if _is_sensitive_path(requested_path, project_root):
        return ToolResult(
            content=f"错误：出于安全原因，禁止读取敏感路径：{path}",
            is_error=True,
            error_type="sensitive_path",
        )

    try:
        file_size = requested_path.stat().st_size
        if file_size > MAX_READ_BYTES:
            return ToolResult(
                content=f"错误：文件过大（{file_size} bytes），最多读取 {MAX_READ_BYTES} bytes。",
                is_error=True,
                error_type="file_too_large",
                details={"actual_bytes": file_size, "max_bytes": MAX_READ_BYTES},
            )

        lines = requested_path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return ToolResult(content="")

        start_index = offset - 1
        if start_index >= len(lines):
            return ToolResult(
                content=f"错误：offset {offset} 超出文件范围（共 {len(lines)} 行）。",
                is_error=True,
                error_type="offset_out_of_range",
                retryable=True,
            )

        end_index = start_index + limit if limit is not None else len(lines)
        result = "\n".join(lines[start_index:end_index])

        if end_index < len(lines):
            remaining = len(lines) - end_index
            next_offset = end_index + 1
            result += f"\n\n[文件还有 {remaining} 行，可使用 offset={next_offset} 继续读取。]"

        return ToolResult(content=result)
    except FileNotFoundError:
        return ToolResult(
            content=f"文件不存在：{path}",
            is_error=True,
            error_type="file_not_found",
            retryable=True,
        )
    except IsADirectoryError:
        return ToolResult(
            content=f"这不是文件：{path}",
            is_error=True,
            error_type="not_a_file",
            retryable=True,
        )
    except UnicodeDecodeError:
        return ToolResult(
            content=f"文件不是 UTF-8 文本：{path}",
            is_error=True,
            error_type="not_utf8_text",
        )


def write_file(path, content, context: RuntimeContext | None = None) -> ToolResult:
    project_root = _project_root(context)
    requested_path = (project_root / path).resolve()
    try:
        requested_path.relative_to(project_root)
    except ValueError:
        return ToolResult(
            content="错误：只能写入 CreatorOS 项目目录内的文件。",
            is_error=True,
            error_type="path_out_of_scope",
            retryable=True,
        )

    if requested_path.exists():
        return ToolResult(
            content=f"错误：文件已存在，为避免覆盖：{path}",
            is_error=True,
            error_type="file_exists",
            retryable=True,
        )

    try:
        requested_path.write_text(content, encoding="utf-8")
        return ToolResult(content=f"已写入文件：{path}")
    except FileNotFoundError:
        return ToolResult(
            content=f"错误：父目录不存在：{path}",
            is_error=True,
            error_type="parent_directory_not_found",
            retryable=True,
        )
    except OSError as error:
        return ToolResult(
            content=f"写入文件失败：{error}",
            is_error=True,
            error_type="write_error",
            details={"exception_type": type(error).__name__},
        )
