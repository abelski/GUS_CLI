from pathlib import Path
from ._sandbox import resolve, sandbox_check

SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file, creating it or overwriting it entirely.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Full content to write."},
            },
            "required": ["path", "content"],
        },
    },
}


def run(path: str, content: str, cwd: str) -> str:
    full_path = resolve(path, cwd)
    if err := sandbox_check(full_path, cwd):
        return err
    try:
        Path(full_path).parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        lines = content.count("\n")
        return f"Wrote {len(content)} bytes ({lines} lines) to {full_path}"
    except Exception as e:
        return f"Error writing file: {e}"
