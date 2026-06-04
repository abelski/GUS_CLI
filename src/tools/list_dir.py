import os
from ._sandbox import resolve, sandbox_check

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "List files and directories at a given path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path (defaults to cwd).",
                }
            },
            "required": [],
        },
    },
}


def run(cwd: str, path: str | None = None) -> str:
    target = resolve(path, cwd) if path else cwd
    if err := sandbox_check(target, cwd):
        return err
    try:
        entries = sorted(os.listdir(target))
        result = []
        for name in entries:
            full = os.path.join(target, name)
            suffix = "/" if os.path.isdir(full) else ""
            result.append(f"{name}{suffix}")
        return "\n".join(result) if result else "(empty directory)"
    except Exception as e:
        return f"Error: {e}"
