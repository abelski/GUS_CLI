"""TaskList — list all session tasks with their status."""
from . import _task_store as _store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "task_list",
        "description": "List all tasks in the session task list with their current status.",
        "parameters": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                    "description": "Optional: show only tasks with this status.",
                },
            },
        },
    },
}

_ICONS = {"completed": "✓", "in_progress": "→", "pending": "○", "blocked": "✗"}


def run(cwd: str, status_filter: str = "") -> str:
    with _store._lock:
        tasks = list(_store._tasks.values())

    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]

    if not tasks:
        return "No tasks." if not status_filter else f"No tasks with status '{status_filter}'."

    lines = []
    for t in tasks:
        icon   = _ICONS.get(t["status"], "?")
        detail = f"  ↳ {t['details']}" if t.get("details") else ""
        lines.append(f"[{t['id']}] {icon} {t['title']}  ({t['status']}){detail}")
    return "\n".join(lines)
