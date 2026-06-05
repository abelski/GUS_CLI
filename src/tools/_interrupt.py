"""Shared cooperative-interrupt flag.

Ctrl+C raises KeyboardInterrupt only on the *main* thread, so tools running in
a ThreadPoolExecutor (parallel tool calls, sub-agents) never see it. This
module exposes a process-wide threading.Event that the main thread sets when it
catches KeyboardInterrupt, and that long-running tools poll so they can abort
promptly from any thread.
"""
import threading

from ._exceptions import ToolInterrupted

interrupt_event = threading.Event()


def set_interrupt() -> None:
    interrupt_event.set()


def clear_interrupt() -> None:
    interrupt_event.clear()


def is_interrupted() -> bool:
    return interrupt_event.is_set()


def raise_if_interrupted(message: str = "Interrupted by user.") -> None:
    """Tools may call this at safe points to bail out when the user hit Ctrl+C."""
    if interrupt_event.is_set():
        raise ToolInterrupted(message)
