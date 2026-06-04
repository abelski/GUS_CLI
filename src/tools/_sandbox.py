"""Shared path helpers used by all file tools."""
import os
from pathlib import Path


def resolve(path: str, cwd: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd) / p
    return str(p.resolve())


def sandbox_check(resolved_path: str, cwd: str) -> str | None:
    """Return an error string if resolved_path escapes the sandbox, else None."""
    root = str(Path(cwd).resolve())
    rp = str(Path(resolved_path).resolve())
    if rp != root and not rp.startswith(root + os.sep):
        return f"Error: path '{resolved_path}' is outside the working directory sandbox ({root})"
    return None
