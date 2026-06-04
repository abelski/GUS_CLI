import subprocess

from ._exceptions import ToolInterrupted

SCHEMA = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Execute a shell command and return its output. "
            "Use for running tests, installing packages, git operations, etc. "
            "Working directory is the project root."
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
        stdout, stderr = proc.communicate(timeout=timeout)
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += stderr
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.communicate()
        return f"Error: command timed out after {timeout}s"
    except KeyboardInterrupt:
        if proc:
            proc.kill()
            proc.communicate()
        raise ToolInterrupted("bash command interrupted by user.")
    except Exception as e:
        return f"Error running command: {e}"
