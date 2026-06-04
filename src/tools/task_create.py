"""TaskCreate — add a new task to the session task list."""
from . import _task_store as _store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "task_create",
        "description": (
            "Create a new task in the session task list. "
            "Use to break complex work into individually-trackable steps."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short task title.",
                },
                "details": {
                    "type": "string",
                    "description": "Optional longer description or acceptance criteria.",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                    "description": "Initial status (default: pending).",
                },
            },
            "required": ["title"],
        },
    },
}


def run(cwd: str, title: str, details: str = "", status: str = "pending") -> str:
    with _store._lock:
        tid = _store.next_id()
        _store._tasks[tid] = {
            "id":      tid,
            "title":   title,
            "details": details,
            "status":  status,
        }
    return f"Created [{tid}] {title}  (status: {status})"
