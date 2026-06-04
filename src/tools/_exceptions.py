class ToolInterrupted(Exception):
    """Raised by a tool when the user presses Ctrl+C mid-execution.
    Caught by agent.run_turn() to abort the current turn and return to the REPL."""
