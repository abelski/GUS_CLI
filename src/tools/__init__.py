"""Tools package — collects schemas and routes execute_tool() calls."""
from typing import Any
from ._exceptions import ToolInterrupted
from . import (
    read_file, write_file, edit_file, bash, glob, grep, list_dir,
    web_search, web_fetch, browser,
    spawn_agent, monitor,
    todo_write,
    task_create, task_list, task_get, task_update,
    ask_user,
)

_TOOLS = [
    read_file, write_file, edit_file, bash, glob, grep, list_dir,
    web_search, web_fetch, browser,
    spawn_agent, monitor,
    todo_write,
    task_create, task_list, task_get, task_update,
    ask_user,
]

TOOL_SCHEMAS: list[dict] = [t.SCHEMA for t in _TOOLS]

_REGISTRY: dict[str, Any] = {t.SCHEMA["function"]["name"]: t for t in _TOOLS}


class _MCPCallable:
    """Thin wrapper so MCP tools fit the same run(cwd, **kwargs) interface."""
    def __init__(self, fn_name: str, manager: Any) -> None:
        self._fn_name = fn_name
        self._manager = manager

    def run(self, cwd: str, **kwargs: Any) -> str:
        return self._manager.call_tool(self._fn_name, kwargs) or f"Error: unknown MCP tool '{self._fn_name}'"


def register_mcp_tools(mcp_manager: Any) -> int:
    """
    Inject MCP tool schemas and callables into the shared registry.
    Called once at startup after MCPManager.start_all().
    Returns the number of tools registered.
    """
    schemas = mcp_manager.get_tool_schemas()
    for schema in schemas:
        fn_name = schema["function"]["name"]
        TOOL_SCHEMAS.append(schema)
        _REGISTRY[fn_name] = _MCPCallable(fn_name, mcp_manager)
    return len(schemas)


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
