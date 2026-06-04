"""Session to-do list — TodoWrite compatible with Claude Code."""
import threading

_lock    = threading.Lock()
_todos:  list[dict] = []
_next_id = [1]

SCHEMA = {
    "type": "function",
    "function": {
        "name": "todo_write",
        "description": (
            "Manage the session to-do list. "
            "Pass a complete replacement list to update tasks, or omit 'todos' to read the current list. "
            "Use status 'pending', 'in_progress', or 'completed'. "
            "Mark tasks completed as you finish them to track multi-step autonomous work."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": (
                        "Replacement task list. Each item: "
                        "'content' (string, required), "
                        "'status' ('pending'|'in_progress'|'completed', default 'pending'), "
                        "'id' (string, auto-assigned if omitted)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":      {"type": "string"},
                            "content": {"type": "string"},
                            "status":  {"type": "string"},
                        },
                        "required": ["content"],
                    },
                },
            },
        },
    },
}


def run(cwd: str, todos: list | None = None) -> str:
    with _lock:
        if todos is not None:
            new_list = []
            for item in todos:
                raw_id = item.get("id")
                if raw_id:
                    tid = str(raw_id)
                else:
                    tid = str(_next_id[0])
                    _next_id[0] += 1
                new_list.append({
                    "id":      tid,
                    "content": str(item.get("content", "")),
                    "status":  item.get("status", "pending"),
                })
            _todos[:] = new_list

        if not _todos:
            return "Task list is empty."

        icons = {"completed": "✓", "in_progress": "→", "pending": "○"}
        lines = []
        for t in _todos:
            icon = icons.get(t["status"], "?")
            lines.append(f"[{t['id']}] {icon} {t['content']}  ({t['status']})")
        return "\n".join(lines)
