"""Time-based routine scheduler for GUS."""
import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent import Agent


# ── Time parsing ───────────────────────────────────────────────────────────

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

def parse_interval(token: str) -> float | None:
    """'5s' → 5.0, '30m' → 1800.0, '1h' → 3600.0, '1d' → 86400.0  else None."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h|d)", token.lower())
    if not m:
        return None
    return float(m.group(1)) * _UNITS[m.group(2)]


def interval_label(seconds: float) -> str:
    """3600.0 → '1h', 90.0 → '1m30s', etc."""
    s = int(seconds)
    if s % 86400 == 0:
        return f"{s // 86400}d"
    if s % 3600 == 0:
        return f"{s // 3600}h"
    if s % 60 == 0:
        return f"{s // 60}m"
    return f"{s}s"


# ── Routine dataclass ──────────────────────────────────────────────────────

@dataclass
class Routine:
    id: str
    prompt: str
    interval: float        # seconds between runs; 0 = every-turn hook
    created_at: float      = field(default_factory=time.time)
    run_count: int         = 0
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    @property
    def is_every_turn(self) -> bool:
        return self.interval == 0

    def label(self) -> str:
        if self.is_every_turn:
            return "every turn"
        return f"every {interval_label(self.interval)}"


# ── Manager ────────────────────────────────────────────────────────────────

class RoutineManager:
    """
    Manages background routines.

    Time-based routines run in daemon threads and acquire a lock before
    calling the agent so they never overlap with each other or with the
    main REPL thread's agent calls.

    Every-turn routines are stored separately and called by the REPL
    before each user prompt.
    """

    def __init__(self, agent: "Agent") -> None:
        self._agent   = agent
        self._lock    = threading.Lock()   # serialises all agent.run_turn() calls
        self._counter = 0
        self.timed:    dict[str, Routine] = {}  # id → Routine (background threads)
        self.every_turn: list[Routine]    = []  # fired before each user prompt

    # ── internal helpers ───────────────────────────────────────────────────

    def _next_id(self) -> str:
        self._counter += 1
        return f"r{self._counter}"

    def _thread_body(self, routine: Routine) -> None:
        """Background thread: sleep, acquire lock, run, repeat."""
        import ui
        while not routine.stop_event.wait(routine.interval):
            with self._lock:
                ui.console.print(
                    f"\n[bold yellow]🕐 Routine [{routine.id}] firing "
                    f"({routine.label()}) …[/bold yellow]"
                )
                try:
                    self._agent.run_turn(routine.prompt)
                    routine.run_count += 1
                except Exception as e:
                    ui.print_error(f"Routine [{routine.id}] error: {e}")

    # ── public API ─────────────────────────────────────────────────────────

    def add_timed(self, prompt: str, interval: float) -> Routine:
        """Start a background routine that fires every `interval` seconds."""
        rid     = self._next_id()
        routine = Routine(id=rid, prompt=prompt, interval=interval)
        t = threading.Thread(target=self._thread_body, args=(routine,), daemon=True)
        routine.thread = t
        self.timed[rid] = routine
        t.start()
        return routine

    def add_every_turn(self, prompt: str) -> Routine:
        """Register a hook that runs before every user prompt."""
        rid     = self._next_id()
        routine = Routine(id=rid, prompt=prompt, interval=0)
        self.every_turn.append(routine)
        return routine

    def stop(self, rid: str) -> bool:
        if rid in self.timed:
            self.timed[rid].stop_event.set()
            del self.timed[rid]
            return True
        for r in self.every_turn:
            if r.id == rid:
                r.stop_event.set()
                self.every_turn.remove(r)
                return True
        return False

    def stop_all(self) -> None:
        for r in self.timed.values():
            r.stop_event.set()
        self.timed.clear()
        for r in self.every_turn:
            r.stop_event.set()
        self.every_turn.clear()

    def list_all(self) -> list[Routine]:
        return list(self.timed.values()) + self.every_turn

    def run_every_turn_hooks(self) -> None:
        """Called by the REPL before presenting the prompt."""
        import ui
        for routine in list(self.every_turn):
            with self._lock:
                ui.console.print(
                    f"\n[bold yellow]🔁 Every-turn hook [{routine.id}] …[/bold yellow]"
                )
                try:
                    self._agent.run_turn(routine.prompt)
                    routine.run_count += 1
                except Exception as e:
                    ui.print_error(f"Hook [{routine.id}] error: {e}")

    def acquire(self):
        """Context manager — used by the REPL to lock agent calls."""
        return self._lock
