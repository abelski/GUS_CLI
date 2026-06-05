import subprocess
import time

from ._exceptions import ToolInterrupted
from ._interrupt import is_interrupted
from ._sandbox import bash_sandbox_check

SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a shell command and return its output. "
            "Use for running tests, installing packages, git operations, etc. "
            "Working directory is the project root. "
            "IMPORTANT: commands must not create or write files outside the working directory. "
            "Redirections, mkdir, touch, cp, mv, and tee targeting paths outside the working "
            "directory are blocked by the sandbox."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30).",
                },
            },
            "required": ["command"],
        },
    },
}


def run(command: str, cwd: str, timeout: int = 30) -> str:
    if err := bash_sandbox_check(command, cwd):
        return err
    proc = None
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        # Poll in short slices so a Ctrl+C from another thread (parallel tool
        # calls / sub-agents set the shared interrupt flag) can kill the child
        # promptly, rather than blocking the whole timeout window.
        deadline = time.monotonic() + timeout
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if is_interrupted():
                    proc.kill()
                    proc.communicate()
                    raise ToolInterrupted("bash command interrupted by user.")
                if time.monotonic() >= deadline:
                    proc.kill()
                    proc.communicate()
                    return f"Error: command timed out after {timeout}s"
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += stderr
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output.strip() or "(no output)"
    except KeyboardInterrupt:
        if proc:
            proc.kill()
            proc.communicate()
        raise ToolInterrupted("bash command interrupted by user.")
    except ToolInterrupted:
        raise  # propagate the cooperative-interrupt signal, don't swallow it
    except Exception as e:
        return f"Error running command: {e}"
