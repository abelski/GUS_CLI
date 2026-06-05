"""Sub-agent tool — delegate a self-contained task to a fresh agent instance.

The actual sub-agent runner is injected by the agent module via
``register_runner`` so this tool never imports ``agent`` — that keeps the
dependency graph acyclic (agent → tools, not tools → agent).
"""
from typing import Callable, Optional

# (task, cwd, context) -> summary string. Wired up at startup by agent.py.
_RUNNER: Optional[Callable[[str, str, str], str]] = None


def register_runner(fn: Callable[[str, str, str], str]) -> None:
    global _RUNNER
    _RUNNER = fn


SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_agent",
        "description": (
            "Spawn an independent sub-agent to handle a well-defined subtask. "
            "The sub-agent has the same tools (read, write, edit, bash, search) and "
            "the same working directory. Use this to parallelise distinct work streams, "
            "isolate risky operations, or delegate research. "
            "Returns a summary of everything the sub-agent did."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear, self-contained description of what the sub-agent should accomplish.",
                },
                "context": {
                    "type": "string",
                    "description": "Extra context, constraints, or files the sub-agent should know about.",
                },
            },
            "required": ["task"],
        },
    },
}


def run(task: str, cwd: str, context: str = "") -> str:
    if _RUNNER is None:
        return "Error: sub-agent runner is not available."
    return _RUNNER(task, cwd, context)
