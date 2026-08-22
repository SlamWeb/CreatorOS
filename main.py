"""Compatibility entrypoint for the CreatorOS runtime.

The implementation now lives under the ``creatoros`` package.  These exports
keep the earlier ``from main import ...`` learning examples working.
"""

from creatoros import config as _config
from creatoros.ai.deepseek import DeepSeekProvider
from creatoros.ai.provider import ModelProvider
from creatoros.ai.types import (
    ModelResponse,
    ModelStreamEvent,
    RuntimeStreamEvent,
    StreamEnd,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallEnd,
)
from creatoros.agent.loop import llm, run_agent as _run_agent
from creatoros.agent.state import AgentState
from creatoros.agent.streaming import stream_llm
from creatoros.session import (
    load_messages as _load_messages,
    new_messages as _new_messages,
    save_messages as _save_messages,
)
from creatoros.session import snapshot as _snapshot
from creatoros.tools import (
    ReadFileArgs,
    Tool,
    ToolResult,
    WriteFileArgs,
    execute_tool_call as _execute_tool_call,
    get_current_date as _get_current_date,
    get_current_time as _get_current_time,
    read_file as _read_file,
    tool_registry,
    tools,
    write_file as _write_file,
)
from creatoros.tools import builtins as _builtins


PROJECT_ROOT = _config.PROJECT_ROOT
SYSTEM_PROMPT = _config.SYSTEM_PROMPT
SESSION_FILE = _config.SESSION_FILE


def _sync_compat_config():
    _snapshot.SESSION_FILE = SESSION_FILE
    _snapshot.SYSTEM_PROMPT = SYSTEM_PROMPT
    _builtins.PROJECT_ROOT = PROJECT_ROOT


def new_messages():
    _sync_compat_config()
    return _new_messages()


def load_messages():
    _sync_compat_config()
    return _load_messages()


def save_messages(messages):
    _sync_compat_config()
    return _save_messages(messages)


def read_file(path, offset=1, limit=None):
    _sync_compat_config()
    return _read_file(path, offset=offset, limit=limit).content


def write_file(path, content):
    _sync_compat_config()
    return _write_file(path, content).content


def get_current_date():
    return _get_current_date().content


def get_current_time():
    return _get_current_time().content


def execute_tool_call(tool_call):
    _sync_compat_config()
    return _execute_tool_call(tool_call)


def run_agent(provider: ModelProvider, on_stream_event=None):
    _sync_compat_config()
    return _run_agent(provider, on_stream_event=on_stream_event)


if __name__ == "__main__":
    from creatoros.cli import main as cli_main

    cli_main()
