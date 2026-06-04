"""
Monitor tool — watch a path or run a condition poll until something happens.
The agent calls this to block until a filesystem event or shell condition is met,
then continues autonomously based on the result.
"""
import os
import glob as _glob
import subprocess
import time
from pathlib import Path

from ._sandbox import resolve, sandbox_check
from ._exceptions import ToolInterrupted

SCHEMA = {
    "type": "function",
    "function": {
        "name": "monitor",
        "description": (
            "Block and watch for a condition to become true, then return what happened. "
            "Use this for autonomous loops: wait for a build to finish, a file to appear, "
            "logs to contain an error, a service to become healthy, etc. "
            "The agent decides when to stop based on the result. "
            "Can be interrupted by the user with Ctrl+C.\n\n"
            "Modes (pick one):\n"
            "  watch + until — filesystem event on a path/pattern\n"
            "  condition      — shell command; done when exit code is 0\n\n"
            "until values: 'created' | 'deleted' | 'modified' | 'exists' | 'gone' | 'stable'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "watch": {
                    "type": "string",
                    "description": (
                        "Path (file or directory) to watch. "
                        "Supports glob patterns like 'logs/*.log'. "
                        "Required unless using condition."
                    ),
                },
                "until": {
                    "type": "string",
                    "enum": ["created", "deleted", "modified", "exists", "gone", "stable"],
                    "description": (
                        "created — new file(s) matching pattern appear; "
                        "deleted — file/dir disappears; "
                        "modified — file content or mtime changes; "
                        "exists   — wait until path exists; "
                        "gone     — wait until path no longer exists; "
                        "stable   — directory stops changing (build finished)."
                    ),
                },
                "condition": {
                    "type": "string",
                    "description": (
                        "Shell command to poll. Done when it exits with code 0. "
                        "stdout/stderr are returned as the result. "
                        "Example: 'curl -sf http://localhost:8080/health'"
                    ),
                },
                "interval": {
                    "type": "number",
                    "description": "Seconds between checks (default 3).",
                },
                "timeout": {
                    "type": "number",
                    "description": "Max seconds to wait before giving up (default 300).",
                },
            },
        },
    },
}


# ── helpers ────────────────────────────────────────────────────────────────

def _snapshot(pattern: str) -> dict[str, float]:
    """Return {filepath: mtime} for all paths matching pattern."""
    paths = _glob.glob(pattern, recursive=True)
    result: dict[str, float] = {}
    for p in paths:
        try:
            result[p] = os.path.getmtime(p)
        except OSError:
            pass
    return result


def _elapsed_label(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f}m"


# ── main ───────────────────────────────────────────────────────────────────

def run(
    cwd: str,
    watch: str = "",
    until: str = "created",
    condition: str = "",
    interval: float = 3.0,
    timeout: float = 300.0,
) -> str:
    import ui

    if not watch and not condition:
        return "Error: provide 'watch' (path/pattern) or 'condition' (shell command)."

    # ── condition mode ─────────────────────────────────────────────────────
    if condition:
        ui.print_info(f"  👁️  monitoring: `{condition}`  (interval {interval}s, timeout {timeout}s)")
        ui.print_info("  Ctrl+C to interrupt.")
        start = time.time()
        last_status_at = start
        try:
            while True:
                elapsed = time.time() - start
                if elapsed > timeout:
                    return f"Timeout after {_elapsed_label(elapsed)} — condition never met: `{condition}`"

                result = subprocess.run(
                    condition, shell=True, capture_output=True,
                    text=True, timeout=max(interval, 10), cwd=cwd,
                )
                if result.returncode == 0:
                    output = (result.stdout + result.stderr).strip()
                    return (
                        f"Condition met after {_elapsed_label(elapsed)}: `{condition}`\n"
                        + (output if output else "(no output)")
                    )

                # periodic status
                if time.time() - last_status_at >= 30:
                    ui.print_info(f"  …still waiting ({_elapsed_label(elapsed)} elapsed)")
                    last_status_at = time.time()

                time.sleep(interval)
        except KeyboardInterrupt:
            raise ToolInterrupted(f"Monitoring interrupted by user after {_elapsed_label(time.time() - start)}.")

    # ── filesystem watch mode ──────────────────────────────────────────────
    # resolve watch path; support relative + glob
    if "*" in watch or "?" in watch:
        watch_pattern = os.path.join(cwd, watch) if not os.path.isabs(watch) else watch
    else:
        resolved = resolve(watch, cwd)
        err = sandbox_check(resolved, cwd)
        if err:
            return err
        watch_pattern = resolved

    ui.print_info(
        f"  👁️  watching [cyan]{watch_pattern}[/cyan] for [{until}]  "
        f"(interval {interval}s, timeout {timeout}s)"
    )
    ui.print_info("  Ctrl+C to interrupt.")

    start          = time.time()
    last_status_at = start
    initial        = _snapshot(watch_pattern)
    stable_snap    = initial.copy()
    stable_since   = start

    try:
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                return f"Timeout after {_elapsed_label(elapsed)} — [{until}] event never occurred on {watch_pattern}"

            current = _snapshot(watch_pattern)

            if until == "exists":
                target = watch_pattern if "*" not in watch_pattern else None
                if target and os.path.exists(target):
                    return f"[exists] Path appeared after {_elapsed_label(elapsed)}: {target}"
                if current:
                    return f"[exists] Matching path(s) appeared after {_elapsed_label(elapsed)}: {', '.join(sorted(current)[:5])}"

            elif until == "gone":
                if not current and initial:
                    return f"[gone] Disappeared after {_elapsed_label(elapsed)}: {', '.join(sorted(initial)[:5])}"
                if not os.path.exists(watch_pattern) and not current:
                    return f"[gone] Path gone after {_elapsed_label(elapsed)}: {watch_pattern}"

            elif until == "created":
                new = set(current) - set(initial)
                if new:
                    return f"[created] New file(s) after {_elapsed_label(elapsed)}: {', '.join(sorted(new)[:5])}"

            elif until == "deleted":
                gone = set(initial) - set(current)
                if gone:
                    return f"[deleted] File(s) removed after {_elapsed_label(elapsed)}: {', '.join(sorted(gone)[:5])}"

            elif until == "modified":
                for path, mtime in current.items():
                    if path in initial and mtime != initial[path]:
                        return f"[modified] File changed after {_elapsed_label(elapsed)}: {path}"
                    if path not in initial:
                        return f"[modified] New file after {_elapsed_label(elapsed)}: {path}"

            elif until == "stable":
                if current != stable_snap:
                    stable_snap  = current.copy()
                    stable_since = time.time()
                elif time.time() - stable_since >= interval * 2:
                    return (
                        f"[stable] Directory unchanged for {_elapsed_label(interval * 2)} "
                        f"after {_elapsed_label(elapsed)} total. "
                        f"{len(current)} file(s) present."
                    )

            if time.time() - last_status_at >= 30:
                ui.print_info(f"  …still watching ({_elapsed_label(elapsed)} elapsed, {len(current)} files)")
                last_status_at = time.time()

            time.sleep(interval)

    except KeyboardInterrupt:
        raise ToolInterrupted(f"Monitoring interrupted by user after {_elapsed_label(time.time() - start)}.")
