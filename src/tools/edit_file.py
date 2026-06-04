from ._sandbox import resolve, sandbox_check

SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Replace an exact string in a file with new content. "
            "old_string must match exactly (including whitespace). "
            "Fails if old_string is not found or appears multiple times."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find and replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}


def run(path: str, old_string: str, new_string: str, cwd: str) -> str:
    full_path = resolve(path, cwd)
    if err := sandbox_check(full_path, cwd):
        return err
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {full_path}"
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {full_path}. "
                "Provide more context to make it unique."
            )
        new_content = content.replace(old_string, new_string, 1)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Edited {full_path} successfully."
    except FileNotFoundError:
        return f"Error: file not found: {full_path}"
    except Exception as e:
        return f"Error editing file: {e}"
