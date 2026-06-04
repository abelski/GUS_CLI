import glob as glob_module
from ._sandbox import resolve, sandbox_check

SCHEMA = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": "Find files matching a glob pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. 'src/**/*.py'.",
                },
                "base_dir": {
                    "type": "string",
                    "description": "Base directory (defaults to cwd).",
                },
            },
            "required": ["pattern"],
        },
    },
}


def run(pattern: str, cwd: str, base_dir: str | None = None) -> str:
    search_base = resolve(base_dir, cwd) if base_dir else cwd
    if err := sandbox_check(search_base, cwd):
        return err
    try:
        matches = glob_module.glob(pattern, root_dir=search_base, recursive=True)
        if not matches:
            return "No files found matching pattern."
        return "\n".join(sorted(matches))
    except Exception as e:
        return f"Error: {e}"
