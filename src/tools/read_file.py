import os

from ._sandbox import resolve, sandbox_check

# Guard rails so a single read can't blow up the context window or stall on a
# huge/binary file.
_DEFAULT_LIMIT = 2000          # lines, when caller doesn't specify
_MAX_BYTES     = 2_000_000     # ~2 MB hard cap on bytes returned

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
    if limit is None:
        limit = _DEFAULT_LIMIT
    start = max(0, offset - 1)
    end = start + max(0, limit)
    try:
        if os.path.isdir(full_path):
            return f"Error: {full_path} is a directory, not a file."
        out: list[str] = []
        byte_budget = _MAX_BYTES
        truncated_bytes = False
        # Stream line by line so we never load a huge file fully into memory,
        # and stop as soon as we've gathered the requested window.
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i < start:
                    continue
                if i >= end:
                    break
                byte_budget -= len(line.encode("utf-8", errors="replace"))
                if byte_budget < 0:
                    truncated_bytes = True
                    break
                out.append(f"{i + 1}\t{line}")
        if not out:
            return "(empty file)" if start == 0 else f"(no lines at offset {offset})"
        result = "".join(out)
        if truncated_bytes:
            result += f"\n[truncated — output exceeded {_MAX_BYTES} bytes]"
        elif len(out) == (end - start):
            result += f"\n[truncated at {limit} lines — pass a higher limit or an offset to read more]"
        return result
    except FileNotFoundError:
        return f"Error: file not found: {full_path}"
    except Exception as e:
        return f"Error reading file: {e}"
