"""Tools package — collects schemas and routes execute_tool() calls."""
from typing import Any
from ._exceptions import ToolInterrupted
from . import read_file, write_file, edit_file, bash, glob, grep, list_dir, web_search, spawn_agent, monitor

_TOOLS = [read_file, write_file, edit_file, bash, glob, grep, list_dir, web_search, spawn_agent, monitor]

TOOL_SCHEMAS: list[dict] = [t.SCHEMA for t in _TOOLS]

_REGISTRY: dict[str, Any] = {t.SCHEMA["function"]["name"]: t for t in _TOOLS}


def execute_tool(name: str, args: dict[str, Any], cwd: str) -> str:
    tool = _REGISTRY.get(name)
    if tool is None:
        return f"Error: unknown tool '{name}'"
    try:
        return tool.run(cwd=cwd, **args)
    except ToolInterrupted:
        raise  # propagate to agent.run_turn
    except TypeError as e:
        return f"Error: invalid arguments for tool '{name}': {e}"
