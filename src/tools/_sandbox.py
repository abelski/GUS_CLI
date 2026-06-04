"""Shared path helpers used by all file tools."""
import os
import re
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


def _is_outside(path_str: str, root: str) -> bool:
    try:
        rp = str(Path(path_str).resolve())
        return rp != root and not rp.startswith(root + os.sep)
    except Exception:
        return False


def bash_sandbox_check(command: str, cwd: str) -> str | None:
    """
    Heuristically detect bash commands that would create or write files outside
    the sandbox. Returns an error string if a violation is found, else None.

    Checks:
      - Output redirections: > /abs/path  or  >> /abs/path
      - mkdir, touch with an absolute path argument
      - tee with an absolute path argument
      - cp / mv where the *last* argument is an absolute path outside cwd
    """
    root = str(Path(cwd).resolve())

    # 1. Output redirections  (> or >>)  to absolute paths
    for m in re.finditer(r'(?<![0-9&<>])>{1,2}\s*(/[^\s;|&"\'`]+)', command):
        target = m.group(1).rstrip("'\"")
        if _is_outside(target, root):
            return (
                f"Error: bash command writes to '{target}' which is outside "
                f"the working directory sandbox ({root}). "
                "All file creation must happen inside the working directory."
            )

    # 2. Commands whose first absolute-path argument is a write target
    for cmd in ("mkdir", "touch", "tee"):
        for m in re.finditer(
            rf'(?:^|[;&|`]|\s){re.escape(cmd)}\s+(?:[^\s]*\s+)*(/[^\s;|&"\'`]+)',
            command,
        ):
            target = m.group(1).rstrip("'\"")
            if _is_outside(target, root):
                return (
                    f"Error: bash command '{cmd}' targets '{target}' which is outside "
                    f"the working directory sandbox ({root}). "
                    "All file creation must happen inside the working directory."
                )

    # 3. cp / mv — destination is the last whitespace-separated token that starts with /
    for cmd in ("cp", "mv"):
        pattern = re.compile(
            rf'(?:^|[;&|`]|\s){re.escape(cmd)}(?:\s+-\S+)*\s+\S+\s+(/[^\s;|&"\'`]+)'
        )
        for m in pattern.finditer(command):
            target = m.group(1).rstrip("'\"")
            if _is_outside(target, root):
                return (
                    f"Error: bash command '{cmd}' copies/moves to '{target}' which is outside "
                    f"the working directory sandbox ({root}). "
                    "All file creation must happen inside the working directory."
                )

    return None
