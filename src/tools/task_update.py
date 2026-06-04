"""TaskUpdate — update or delete a task."""
from . import _task_store as _store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "task_update",
        "description": (
            "Update a task's status, title, or details. Pass only the fields to change. "
            "Set delete=true to remove the task entirely."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID to update (e.g. T001).",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                    "description": "New status.",
                },
                "title": {
                    "type": "string",
                    "description": "New title.",
                },
                "details": {
                    "type": "string",
                    "description": "New details.",
                },
                "delete": {
                    "type": "boolean",
                    "description": "Set true to delete the task.",
                },
            },
            "required": ["task_id"],
        },
    },
}


def run(cwd: str, task_id: str, status: str = "", title: str = "",
        details: str = "", delete: bool = False) -> str:
    with _store._lock:
        if task_id not in _store._tasks:
            return f"Error: no task with ID '{task_id}'."
        if delete:
            t = _store._tasks.pop(task_id)
            return f"Deleted [{task_id}] {t['title']}."
        task = _store._tasks[task_id]
        if status:
            task["status"] = status
        if title:
            task["title"] = title
        if details:
            task["details"] = details
    return f"Updated [{task_id}] {task['title']}  (status: {task['status']})"
