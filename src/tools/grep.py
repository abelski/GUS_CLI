import shutil
import subprocess
from ._sandbox import resolve, sandbox_check

# Directories that are large, generated, or vendored — skipped by default so
# searches stay fast and results stay signal-rich.
_PRUNE_DIRS = (
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".tox", "target",
)

SCHEMA = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": "Search for a pattern in files.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (defaults to cwd).",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive search (default true).",
                },
                "include": {
                    "type": "string",
                    "description": "File glob filter, e.g. '*.py'.",
                },
            },
            "required": ["pattern"],
        },
    },
}


def run(
    pattern: str,
    cwd: str,
    path: str | None = None,
    case_sensitive: bool = True,
    include: str | None = None,
) -> str:
    search_path = resolve(path, cwd) if path else cwd
    if err := sandbox_check(search_path, cwd):
        return err

    # Prefer ripgrep when available: faster and honours .gitignore automatically.
    if shutil.which("rg"):
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if not case_sensitive:
            cmd.append("-i")
        if include:
            cmd += ["--glob", include]
        for d in _PRUNE_DIRS:
            cmd += ["--glob", f"!{d}/"]
        cmd += [pattern, search_path]
    else:
        flags = [] if case_sensitive else ["-i"]
        include_flags = ["--include", include] if include else []
        prune_flags = [f"--exclude-dir={d}" for d in _PRUNE_DIRS]
        cmd = ["grep", "-rn", "--color=never"] + flags + include_flags + prune_flags + [pattern, search_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = result.stdout.strip()
        if not out:
            return "No matches found."
        lines = out.split("\n")
        if len(lines) > 100:
            return "\n".join(lines[:100]) + f"\n... ({len(lines) - 100} more lines truncated)"
        return out
    except Exception as e:
        return f"Error: {e}"
