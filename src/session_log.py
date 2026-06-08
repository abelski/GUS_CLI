"""Per-project session transcript.

Writes a clean, human-readable trace of what GUS does — the user's prompts, every
tool call with its arguments, each result, GUS's replies, and notable events
(rate-limit waits, model fallbacks) — into ``<cwd>/.gus/sessions/session-<ts>.log``.

This is distinct from ``gus.log`` (global DEBUG noise in the config dir): the
session log lives in the working directory so you can read back exactly what the
agent did on this project and tune skills / prompts / findings accordingly.

Logging is best-effort and never raises into a turn. Writes are appended under a
lock so parallel tool calls (worker threads) interleave cleanly.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

_ARGS_MAX = 800
_RESULT_MAX = 2000
_ASSISTANT_MAX = 4000


def session_log_enabled() -> bool:
    """Whether session logging is on. Default on; read live from the env."""
    return os.environ.get("AGENT_SESSION_LOG", "1").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


class SessionLogger:
    """Appends a readable transcript for one GUS session to the working dir."""

    def __init__(self, cwd: str, model: str) -> None:
        self._lock = threading.Lock()
        ts = datetime.now()
        self._dir = Path(cwd) / ".gus" / "sessions"
        self._path = self._dir / f"session-{ts:%Y%m%d-%H%M%S}.log"
        self._ready = False
        self._write(
            f"{'=' * 72}\n"
            f"GUS session {ts:%Y-%m-%d %H:%M:%S}  ·  model={model}  ·  cwd={cwd}\n"
            f"{'=' * 72}"
        )

    @property
    def path(self) -> Path:
        return self._path

    # ── internals ───────────────────────────────────────────────────────────
    @staticmethod
    def _t() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _write(self, text: str) -> None:
        try:
            with self._lock:
                if not self._ready:
                    self._dir.mkdir(parents=True, exist_ok=True)
                    self._ready = True
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(text + "\n")
        except Exception:
            pass  # a logging failure must never break the user's turn

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = text or ""
        if len(text) <= limit:
            return text
        return text[:limit] + f" …(+{len(text) - limit} chars)"

    # ── events ──────────────────────────────────────────────────────────────
    def user(self, msg: str) -> None:
        self._write(f"\n[{self._t()}] ▸ USER\n{(msg or '').strip()}")

    def tool_call(self, name: str, args: dict) -> None:
        try:
            a = json.dumps(args, ensure_ascii=False)
        except Exception:
            a = str(args)
        self._write(f"[{self._t()}]   ⟶ {name} {self._clip(a, _ARGS_MAX)}")

    def tool_result(self, name: str, result: str, is_error: bool = False) -> None:
        body = self._clip(result, _RESULT_MAX).replace("\n", "\n        ")
        self._write(f"[{self._t()}]   {'✗' if is_error else '✓'} {name} → {body}")

    def assistant(self, text: str) -> None:
        self._write(f"[{self._t()}] ◂ GUS\n{self._clip((text or '').strip(), _ASSISTANT_MAX)}")

    def event(self, msg: str) -> None:
        self._write(f"[{self._t()}]   · {msg}")

    def turn_end(self, tool_calls: int, model: str, secs: float) -> None:
        self._write(
            f"[{self._t()}] ── turn end · {tool_calls} tool call(s) · "
            f"model={model} · {secs:.1f}s"
        )
