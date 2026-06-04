"""TaskGet — retrieve full details for a specific task."""
from . import _task_store as _store

SCHEMA = {
    "type": "function",
    "function": {
        "name": "task_get",
        "description": "Retrieve full details for a specific task by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID returned by task_create (e.g. T001).",
                },
            },
            "required": ["task_id"],
        },
    },
}


def run(cwd: str, task_id: str) -> str:
    with _store._lock:
        task = _store._tasks.get(task_id)
    if task is None:
        return f"Error: no task with ID '{task_id}'."
    lines = [
        f"ID:     {task['id']}",
        f"Title:  {task['title']}",
        f"Status: {task['status']}",
    ]
    if task.get("details"):
        lines.append(f"Detail: {task['details']}")
    return "\n".join(lines)
