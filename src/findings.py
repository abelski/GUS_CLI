"""Persistent per-project findings memory.

GUS records what it learns while working — key successes, pitfalls, and
problems-with-solutions — into ``<cwd>/.gus/findings.md`` at the end of each
turn, and feeds that file back into the system prompt on the next session so
the agent benefits from past work.

Storage is plain markdown (mirrors the ``agents.md`` convention) so it can be
injected into the prompt verbatim and read/edited by a human. The capture pass
is best-effort: any failure here is logged and swallowed so it can never break
the user's turn.
"""
from datetime import datetime
from pathlib import Path

from config import CONFIG_DIR, save_env_var, setup_logging

log = setup_logging()

FINDINGS_FILENAME = "findings.md"
_HEADER = "# GUS Findings\n"
_ENABLED_ENV = "AGENT_FINDINGS_ENABLED"
# Cap how much of the file we inject into the prompt so it can't grow unbounded.
_MAX_INJECT_CHARS = 6000


def findings_path(cwd: str) -> Path:
    """Location of the per-project findings file."""
    return Path(cwd) / ".gus" / FINDINGS_FILENAME


def findings_enabled() -> bool:
    """Whether findings capture is on. Default on; read live from the env."""
    import os
    return os.environ.get(_ENABLED_ENV, "1").strip().lower() not in ("0", "false", "no", "off", "")


def set_enabled(on: bool) -> None:
    """Persist the on/off toggle to .env (survives restarts, like every setting)."""
    save_env_var(_ENABLED_ENV, "1" if on else "0")


def load(cwd: str) -> str:
    """Return the findings file contents, trimmed to the most recent entries.

    Returns ``""`` when there are no findings yet.
    """
    path = findings_path(cwd)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        log.warning("findings: could not read %s: %s", path, e)
        return ""
    if len(text) <= _MAX_INJECT_CHARS:
        return text
    # Keep the tail, then snap to the first whole entry so we don't inject a
    # half-truncated one. Re-prepend the header for readability.
    tail = text[-_MAX_INJECT_CHARS:]
    cut = tail.find("\n### ")
    if cut != -1:
        tail = tail[cut + 1:]
    return _HEADER + "\n" + tail


def append(cwd: str, task: str, markdown: str) -> None:
    """Append a dated finding entry under a ``### <date> — <task>`` heading."""
    path = findings_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    heading = f"### {stamp} — {task.strip().splitlines()[0][:80]}"
    block = f"\n{heading}\n{markdown.strip()}\n"
    prefix = "" if path.is_file() else _HEADER
    with path.open("a", encoding="utf-8") as f:
        f.write(prefix + block)


def _turn_did_work(new_messages: list[dict]) -> bool:
    """True if the turn made any tool call — pure-chat turns aren't worth saving."""
    return any(m.get("role") == "assistant" and m.get("tool_calls") for m in new_messages)


def _transcript(new_messages: list[dict]) -> str:
    """Render the turn's messages into a compact transcript for the extractor."""
    # Map tool_call_id -> tool name so tool results can be labelled.
    names: dict[str, str] = {}
    for m in new_messages:
        for tc in m.get("tool_calls") or []:
            names[tc.get("id", "")] = tc.get("function", {}).get("name", "tool")

    lines: list[str] = []
    for m in new_messages:
        role = m.get("role", "")
        content = m.get("content") or ""
        if role == "user":
            lines.append(f"USER: {content}")
        elif role == "assistant":
            if content:
                lines.append(f"ASSISTANT: {content}")
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                lines.append(f"ASSISTANT called {fn.get('name', 'tool')}({fn.get('arguments', '')[:300]})")
        elif role == "tool":
            name = names.get(m.get("tool_call_id", ""), "tool")
            lines.append(f"RESULT[{name}]: {str(content)[:500]}")
    return "\n".join(lines)


def persist_turn(agent, new_messages: list[dict], task: str) -> None:
    """End-of-turn capture: extract findings from a completed turn and save them.

    Best-effort and bounded: no-ops when disabled or when the turn did no real
    work, and never raises into the caller.
    """
    try:
        if not findings_enabled() or not _turn_did_work(new_messages):
            return
        transcript = _transcript(new_messages)
        if not transcript.strip():
            return
        result = agent.extract_findings(transcript)
        result = (result or "").strip()
        if not result or result.upper() == "NONE":
            return
        append(agent.cwd, task, result)
        log.info("findings: saved entry for task %r", task[:60])
    except Exception as e:  # pragma: no cover - defensive; capture must never break a turn
        log.warning("findings: capture failed: %s", e)


# Re-export for callers that want the config dir (e.g. for diagnostics).
__all__ = [
    "findings_path", "findings_enabled", "set_enabled",
    "load", "append", "persist_turn", "CONFIG_DIR",
]
