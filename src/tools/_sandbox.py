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


def _is_outside(path_str: str, root: str, cwd: str | None = None) -> bool:
    """True if path_str (absolute, or relative to cwd) resolves outside root."""
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = Path(cwd or root) / p
        rp = str(p.resolve())
        return rp != root and not rp.startswith(root + os.sep)
    except Exception:
        return False


# A token that could escape: absolute path, ~ expansion, or anything with '..'.
_ESCAPE_HINT = re.compile(r'(^/|^~|\.\.)')


def bash_sandbox_check(command: str, cwd: str) -> str | None:
    """
    Best-effort heuristic guard: detect bash commands that would create, write,
    or move files outside the sandbox, or change directory out of it.

    This is advisory, not a security boundary — it cannot catch every escape
    (e.g. via interpreters, eval, or variable expansion). The hard guarantee is
    that the file tools (read/write/edit) resolve real paths and refuse escapes.

    Checks (absolute paths AND relative '..'/'~' paths that resolve outside cwd):
      - Output redirections: >, >>
      - mkdir, touch, tee write targets
      - cp / mv destinations
      - cd / pushd into a directory outside the sandbox
    """
    root = str(Path(cwd).resolve())

    def _escapes(tok: str) -> bool:
        tok = tok.strip().rstrip("'\"").lstrip("'\"")
        if not tok or not _ESCAPE_HINT.search(tok):
            return False
        if tok.startswith("~"):
            tok = os.path.expanduser(tok)
        return _is_outside(tok, root, cwd)

    # 1. Output redirections  (> or >>)
    for m in re.finditer(r'(?<![0-9&<>])>{1,2}\s*([^\s;|&"\'`]+)', command):
        if _escapes(m.group(1)):
            return (
                f"Error: bash command writes to '{m.group(1)}' which is outside "
                f"the working directory sandbox ({root}). "
                "All file creation must happen inside the working directory."
            )

    # 2. mkdir / touch / tee — any argument that escapes
    for cmd in ("mkdir", "touch", "tee"):
        for m in re.finditer(
            rf'(?:^|[;&|`]|\s){re.escape(cmd)}\s+([^\n;|&]+)', command,
        ):
            for tok in m.group(1).split():
                if tok.startswith("-"):
                    continue
                if _escapes(tok):
                    return (
                        f"Error: bash command '{cmd}' targets '{tok}' which is outside "
                        f"the working directory sandbox ({root}). "
                        "All file creation must happen inside the working directory."
                    )

    # 3. cp / mv — destination (last token) escapes
    for cmd in ("cp", "mv"):
        pattern = re.compile(
            rf'(?:^|[;&|`]|\s){re.escape(cmd)}((?:\s+[^\n;|&]+)+)'
        )
        for m in pattern.finditer(command):
            toks = [t for t in m.group(1).split() if not t.startswith("-")]
            if toks and _escapes(toks[-1]):
                return (
                    f"Error: bash command '{cmd}' copies/moves to '{toks[-1]}' which is outside "
                    f"the working directory sandbox ({root}). "
                    "All file creation must happen inside the working directory."
                )

    # 4. cd / pushd out of the sandbox — would let later relative writes escape
    for cmd in ("cd", "pushd"):
        for m in re.finditer(rf'(?:^|[;&|`]|&&|\|\|)\s*{re.escape(cmd)}\s+([^\s;|&]+)', command):
            if _escapes(m.group(1)):
                return (
                    f"Error: bash command changes directory to '{m.group(1)}' which is outside "
                    f"the working directory sandbox ({root}). Stay within the working directory."
                )

    return None
