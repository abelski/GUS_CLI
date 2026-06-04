"""Shared in-process task store for task_create/get/list/update."""
import threading

_lock:    threading.Lock = threading.Lock()
_tasks:   dict[str, dict] = {}
_counter: list[int] = [0]


def next_id() -> str:
    _counter[0] += 1
    return f"T{_counter[0]:03d}"
