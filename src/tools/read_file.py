from ._sandbox import resolve, sandbox_check

SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read the contents of a file at the given path. "
            "Use this to understand existing code before editing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number (1-based) to start reading from. Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. Optional.",
                },
            },
            "required": ["path"],
        },
    },
}


def run(path: str, cwd: str, offset: int = 1, limit: int | None = None) -> str:
    full_path = resolve(path, cwd)
    if err := sandbox_check(full_path, cwd):
        return err
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        start = max(0, offset - 1)
        end = start + limit if limit else len(lines)
        selected = lines[start:end]
        numbered = "".join(f"{start + i + 1}\t{line}" for i, line in enumerate(selected))
        return numbered if numbered else "(empty file)"
    except FileNotFoundError:
        return f"Error: file not found: {full_path}"
    except Exception as e:
        return f"Error reading file: {e}"
