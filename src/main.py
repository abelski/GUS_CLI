#!/usr/bin/env python3
"""GUS — CLI agent entry point."""
import os
import platform
import signal
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))

import argparse

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style

from pathlib import Path

from openai import AuthenticationError

import ui

from config import (
    get_client, DEFAULT_MODEL, WORKING_DIR, CONFIG_DIR, MAX_GOAL_ITERATIONS,
    get_free_models, fetch_free_models, save_free_models, save_env_var,
)
from agent import Agent
import findings
from context import load_context, context_fingerprint, ProjectContext, Command
from loop import RoutineManager, parse_interval, interval_label
from mcp_client import MCPManager
from tools import register_mcp_tools
from tools._interrupt import is_interrupted, set_interrupt

_ENV_FILE = str(CONFIG_DIR / ".env")


def _ensure_gus_dirs(cwd: str) -> None:
    import shutil
    root = Path(cwd)
    for d in [".gus/skills", ".gus/commands"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    # Copy bundled skills (shipped next to GUS source) into the working dir
    # so they are within the sandbox when the agent tries to read them.
    bundled = Path(__file__).parent.parent / ".gus" / "skills"
    if bundled.is_dir():
        for skill_dir in bundled.iterdir():
            if not skill_dir.is_dir():
                continue
            dest = root / ".gus" / "skills" / skill_dir.name
            if not dest.exists():
                shutil.copytree(skill_dir, dest)


class _QuitSignal(Exception):
    """Raised by /exit or /quit to break out of the REPL loop cleanly."""


HISTORY_FILE = os.path.expanduser("~/.gus_history")
PROMPT_STYLE = Style.from_dict({"prompt": "bold ansicyan"})

_BUILTIN_COMMANDS: list[tuple[str, str]] = [
    ("/help",          "show all commands"),
    ("/clear",         "clear conversation history"),
    ("/new",           "clear conversation history"),
    ("/reset",         "clear conversation history"),
    ("/compact",       "summarise history into one message"),
    ("/plan",          "plan mode: analyse only, no writes"),
    ("/go",            "execute the current plan"),
    ("/agent",         "switch back to agent mode"),
    ("/btw",           "ask a side question (no history change)"),
    ("/recap",         "one-sentence session summary"),
    ("/rename",        "name this session"),
    ("/export",        "export conversation to clipboard or file"),
    ("/copy",          "copy last response to clipboard"),
    ("/cwd",           "show or change working directory"),
    ("/model",         "pick model from list (or /model <id>)"),
    ("/settings",      "settings screen (model selection)"),
    ("/findings",      "view, clear, or toggle the findings memory"),
    ("/log",           "show this session's log path and recent entries"),
    ("/usage",         "token usage and session stats"),
    ("/cost",          "token usage and session stats"),
    ("/stats",         "token usage and session stats"),
    ("/context",       "context window breakdown by category"),
    ("/goal",          "set autonomous goal; GUS loops until met"),
    ("/loop",          "repeat or schedule a routine"),
    ("/skills",        "list all loaded skills"),
    ("/reload-skills", "rescan .gus/commands/ and .gus/skills/ from disk"),
    ("/mcp",           "list MCP servers and tools"),
    ("/exit",          "quit"),
    ("/quit",          "quit"),
]


class _GusCompleter(Completer):
    """Complete slash commands; reads ctx live so new commands are picked up."""

    def __init__(self, ctx: "ProjectContext") -> None:
        self._ctx = ctx

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        word = text.lower()
        seen: set[str] = set()
        for cmd, desc in _BUILTIN_COMMANDS:
            if cmd.startswith(word) and cmd not in seen:
                seen.add(cmd)
                yield Completion(cmd, start_position=-len(word),
                                 display=cmd, display_meta=desc)
        for name, skill in self._ctx.skills.items():
            cmd = "/" + name
            if cmd.startswith(word) and cmd not in seen:
                seen.add(cmd)
                yield Completion(cmd, start_position=-len(word),
                                 display=cmd, display_meta=skill.description)
        for name, skill in self._ctx.agent_skills.items():
            cmd = "/" + name
            if cmd.startswith(word) and cmd not in seen:
                seen.add(cmd)
                yield Completion(cmd, start_position=-len(word),
                                 display=cmd, display_meta=skill.description)


# ── Argument parsing ───────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUS — AI assistant powered by OpenRouter",
    )
    parser.add_argument("prompt", nargs="?", help="One-shot prompt (non-interactive).")
    parser.add_argument("--cwd", default=WORKING_DIR,
                        help="Working directory for file and shell operations.")
    return parser.parse_args()


# ── Command execution ──────────────────────────────────────────────────────

def _run_command(cmd: Command, args: str, agent: Agent) -> None:
    shell_output = ""
    if cmd.shell:
        ui.print_info(f"  running shell: {cmd.shell}")
        shell_output = cmd.run_shell(agent.cwd)
        if shell_output:
            ui.print_info(shell_output[:500] + ("…" if len(shell_output) > 500 else ""))
    prompt = cmd.build_prompt(args=args, shell_output=shell_output)
    agent.run_turn(prompt)


def _run_fixed_loop(prompt_or_cmd, args: str, agent: Agent,
                    iterations: int, ctx: ProjectContext) -> None:
    """Repeat a prompt or Command a fixed number of times."""
    is_cmd = isinstance(prompt_or_cmd, Command)
    for i in range(1, iterations + 1):
        ui.console.print(
            f"\n[bold yellow]━━━ 🦆 Loop {i}/{iterations} ━━━[/bold yellow]"
        )
        try:
            if is_cmd:
                _run_command(prompt_or_cmd, args, agent)
            else:
                text = prompt_or_cmd + (f"\n\n{args}" if args else "")
                ui.print_user(text)
                agent.run_turn(text)
        except KeyboardInterrupt:
            ui.print_flying_away()
            return
        # A Ctrl+C during a tool returns from run_turn cleanly (history kept)
        # rather than raising — break the whole loop instead of rolling into
        # the next iteration.
        if is_interrupted():
            ui.console.print("\n[dim]*Loop interrupted by user — stopping.*[/dim]")
            return
    ui.console.print("[bold yellow]━━━ 🦆 Loop done ━━━[/bold yellow]")


# ── /loop dispatcher ───────────────────────────────────────────────────────

def handle_loop(rest: str, agent: Agent, ctx: ProjectContext,
                routines: RoutineManager) -> None:
    """
    /loop list
    /loop stop <id>
    /loop every <prompt>           — every-turn hook
    /loop <time> <prompt|/cmd>     — background schedule  (1h, 30m, 1d, 5s …)
    /loop <n>    <prompt|/cmd>     — fixed iterations     (integer)
    /loop        <prompt>          — fixed 3 iterations (default)
    """
    if not rest or rest == "list":
        _print_routines(routines)
        return

    tokens = rest.split(None, 1)
    first  = tokens[0].lower()
    tail   = tokens[1] if len(tokens) > 1 else ""

    # /loop stop <id>
    if first == "stop":
        rid = tail.strip()
        if not rid:
            ui.print_error("Usage: /loop stop <id>")
            return
        if routines.stop(rid):
            ui.print_info(f"  Routine [{rid}] stopped.")
        else:
            ui.print_error(f"No routine with id '{rid}'.")
        return

    # /loop every <prompt>
    if first == "every":
        if not tail:
            ui.print_error("Usage: /loop every <prompt>")
            return
        r = routines.add_every_turn(tail)
        ui.print_info(f"  [{r.id}] Hook registered — runs before every prompt.")
        return

    # /loop <time> ...
    interval = parse_interval(first)
    if interval is not None:
        if not tail:
            ui.print_error(f"Usage: /loop {first} <prompt>")
            return
        prompt_text, cmd_obj, cmd_args = _resolve_prompt_or_cmd(tail, ctx, agent)
        final_prompt = prompt_text
        if cmd_obj and cmd_obj.shell:
            final_prompt = f"__cmd__{cmd_obj.name}__{cmd_args}"
        r = routines.add_timed(final_prompt, interval)
        if cmd_obj:
            routines.timed[r.id]._cmd_obj  = cmd_obj   # type: ignore[attr-defined]
            routines.timed[r.id]._cmd_args = cmd_args  # type: ignore[attr-defined]
        lbl = interval_label(interval)
        ui.print_info(f"  [{r.id}] Routine scheduled — fires every {lbl}.")
        return

    # /loop <n> ...
    if first.isdigit():
        n    = int(first)
        if not tail:
            ui.print_error(f"Usage: /loop {n} <prompt>")
            return
        _, cmd_obj, cmd_args = _resolve_prompt_or_cmd(tail, ctx, agent)
        if cmd_obj:
            _run_fixed_loop(cmd_obj, cmd_args, agent, n, ctx)
        else:
            _run_fixed_loop(tail, "", agent, n, ctx)
        return

    # /loop <bare prompt>  → default 3 iterations
    _run_fixed_loop(rest, "", agent, 3, ctx)


def _resolve_prompt_or_cmd(
    text: str, ctx: ProjectContext, agent: Agent
) -> tuple[str, Command | None, str]:
    """If text starts with /<name>, return (prompt, cmd, args). Else (text, None, '')."""
    if text.startswith("/"):
        parts    = text.split(None, 1)
        name     = parts[0].lstrip("/").lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        if name in ctx.skills:
            return ("", ctx.skills[name], cmd_args)
        ui.print_error(f"Unknown command: /{name}")
    return (text, None, "")


def _print_routines(routines: RoutineManager) -> None:
    all_r = routines.list_all()
    if not all_r:
        ui.print_info("  No active routines.")
        return
    ui.console.print("\n[bold yellow]Active routines:[/bold yellow]")
    for r in all_r:
        ui.console.print(
            f"  [cyan][{r.id}][/cyan]  {r.label():<18}  "
            f"runs: {r.run_count}   "
            f"[dim]{r.prompt[:60]}{'…' if len(r.prompt) > 60 else ''}[/dim]"
        )


# ── Goal helpers ───────────────────────────────────────────────────────────

def _check_goal_satisfied(agent: Agent) -> bool:
    """Ask the model (non-history) whether the current goal has been met."""
    if not agent.goal:
        return True
    answer = agent.btw(
        f"Has this goal been achieved: \"{agent.goal}\"? "
        "Look at the full conversation above and reply ONLY with YES or NO."
    )
    return answer.strip().upper().startswith("YES")


def _run_goal_loop(agent: Agent, routines: RoutineManager) -> None:
    """After a turn completes, auto-continue if goal is not yet satisfied."""
    iterations = 0
    while agent.goal:
        if iterations >= MAX_GOAL_ITERATIONS:
            ui.print_warning(
                f"  Goal loop hit the {MAX_GOAL_ITERATIONS}-iteration cap without being "
                f"satisfied — pausing. Goal preserved; use /goal clear to cancel."
            )
            break
        iterations += 1
        ui.print_info(f"  Checking goal ({iterations}/{MAX_GOAL_ITERATIONS}): {agent.goal!r}")
        try:
            satisfied = _check_goal_satisfied(agent)
        except Exception as e:
            ui.print_error(f"Goal check failed: {e} — pausing goal loop.")
            break
        if satisfied:
            ui.print_goal_achieved(agent.goal)
            agent.goal = None
            break
        ui.print_info("  Goal not yet met — continuing…")
        try:
            with routines.acquire():
                agent.run_turn(f"Continue working toward the goal: {agent.goal}")
        except KeyboardInterrupt:
            ui.console.print("\n[dim]*Goal loop interrupted — goal preserved. Use /goal clear to cancel.*[/dim]")
            break
        except Exception as e:
            ui.print_confused(str(e))
            break
        if is_interrupted():
            ui.console.print("\n[dim]*Goal loop interrupted — goal preserved. Use /goal clear to cancel.*[/dim]")
            break


# ── Clipboard helper ───────────────────────────────────────────────────────

def _confirm(question: str) -> bool:
    """Ask a yes/no confirmation. Denies by default when non-interactive
    (no TTY, or NO_QUESTIONS set) so destructive commands never block a
    headless/routine run waiting on stdin."""
    if os.environ.get("NO_QUESTIONS") == "1" or not sys.stdin.isatty():
        ui.print_info(f"{question} [auto-declined — non-interactive]")
        return False
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        elif system == "Windows":
            subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
        else:
            try:
                subprocess.run(["xclip", "-selection", "clipboard"],
                               input=text.encode(), check=True)
            except FileNotFoundError:
                subprocess.run(["xsel", "--clipboard", "--input"],
                               input=text.encode(), check=True)
        return True
    except Exception:
        return False


# ── Export helper ──────────────────────────────────────────────────────────

def _export_conversation(agent: Agent) -> str:
    """Render conversation history as markdown text."""
    lines = []
    for msg in agent.history:
        role    = msg["role"].upper()
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        if role == "TOOL":
            lines.append(f"### TOOL RESULT\n\n```\n{content}\n```")
        else:
            lines.append(f"## {role}\n\n{content}")
    return "\n\n---\n\n".join(lines)


# ── MCP helper ────────────────────────────────────────────────────────────

def _handle_mcp(mcp: "MCPManager | None") -> None:
    if mcp is None or mcp.server_count == 0:
        ui.print_info("  No MCP servers configured. Add servers to .gus/mcp.json.")
        return
    servers = mcp.list_servers()
    ui.console.print("\n[bold yellow]MCP servers:[/bold yellow]")
    for s in servers:
        status = "[green]running[/green]" if s["running"] else "[red]stopped[/red]"
        tools  = ", ".join(s["tools"]) if s["tools"] else "(no tools)"
        ui.console.print(f"  [cyan]{s['name']}[/cyan]  {status}  —  {tools}")


# ── Settings ───────────────────────────────────────────────────────────────

def _apply_model(agent: Agent, model: str, persist: bool = True) -> None:
    """Switch the live agent model and (optionally) persist it to .env."""
    agent.model = model
    if persist:
        save_env_var("AGENT_MODEL", model)


def _switch_model_by_id(agent: Agent, model_id: str) -> None:
    """Switch to an explicit model id from `/model <id>`. Warns and asks to
    confirm when the id isn't in the known free-model list (likely a typo or a
    paid model) so a fat-fingered id doesn't silently break the next turn."""
    model_id = model_id.strip()
    known = {mid for mid, _, _ in get_free_models()}
    if model_id not in known:
        ui.print_warning(
            f"'{model_id}' isn't in the known free-model list — "
            "it may be a paid model or a typo."
        )
        if not _confirm("  Switch to it anyway?"):
            ui.print_info("  Model unchanged.")
            return
    _apply_model(agent, model_id)
    ui.print_info(f"  Model switched to: {agent.model} [dim](saved to .env)[/dim]")


def _numbered_model_select(models: list[tuple[str, str, str]],
                           current: str) -> str | None:
    """Fallback picker (numbered input) for terminals that can't run the
    full-screen dialog. Returns the chosen model id, or None to cancel."""
    ui.print_model_picker(models, current)
    try:
        choice = input("  Select model #: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice or choice in ("q", "quit", "cancel"):
        return None
    if not choice.isdigit() or not (1 <= int(choice) <= len(models)):
        ui.print_error(f"Enter a number 1–{len(models)}.")
        return None
    return models[int(choice) - 1][0]


def _interactive_model_select(models: list[tuple[str, str, str]],
                              current: str) -> str | None:
    """Full-screen searchable model picker (type to filter · ↑/↓ · Enter · Esc).
    Pre-selects and scrolls to the current model, which is marked with ●.
    Returns the chosen id, or None on cancel. Falls back to a numbered picker
    if the full-screen application can't be drawn."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style as PTStyle

    # Selection state, shared with the render/key-binding closures below.
    state: dict = {"filtered": list(models), "index": 0}
    for i, (mid, _, _) in enumerate(models):
        if mid == current:
            state["index"] = i
            break

    search = Buffer(multiline=False)

    def refilter(_buf=None) -> None:
        q = search.text.strip().lower()
        state["filtered"] = [
            m for m in models
            if not q or q in m[0].lower() or q in m[1].lower() or q in m[2].lower()
        ]
        # Filtered set shrank — clamp the cursor so it stays in range.
        if state["index"] >= len(state["filtered"]):
            state["index"] = max(0, len(state["filtered"]) - 1)

    search.on_text_changed += refilter

    def list_fragments():
        filt = state["filtered"]
        if not filt:
            return [("class:dim", "  No models match — backspace to widen the search.")]
        frags: list[tuple[str, str]] = []
        for i, (mid, label, note) in enumerate(filt):
            selected = i == state["index"]
            if selected:
                # Magic token: makes the Window scroll to keep this row visible.
                frags.append(("[SetCursorPosition]", ""))
            marker = "●" if mid == current else " "
            pointer = "❯ " if selected else "  "
            style = "class:selected" if selected else "class:item"
            frags.append((style, f"{pointer}{marker} {label}  [{mid}]  {note}\n"))
        return frags

    def count_text():
        n, total = len(state["filtered"]), len(models)
        shown = f"{n}/{total}" if n != total else str(total)
        return [("class:dim", f"  {shown} free models · ↑/↓ move · Enter select · Esc cancel")]

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if state["filtered"]:
            state["index"] = (state["index"] - 1) % len(state["filtered"])

    @kb.add("down")
    def _(event):
        if state["filtered"]:
            state["index"] = (state["index"] + 1) % len(state["filtered"])

    @kb.add("enter")
    def _(event):
        filt = state["filtered"]
        event.app.exit(result=filt[state["index"]][0] if filt else None)

    @kb.add("escape")
    @kb.add("c-c")
    def _(event):
        event.app.exit(result=None)

    layout = Layout(HSplit([
        Window(FormattedTextControl(
            [("class:title", " 🦆 GUS — Select Model ")]), height=1),
        VSplit([
            Window(FormattedTextControl([("class:label", "  Filter: ")]),
                   width=10, height=1),
            Window(BufferControl(buffer=search), height=1),
        ]),
        Window(height=1, char="─", style="class:dim"),
        Window(FormattedTextControl(list_fragments), wrap_lines=False,
               height=Dimension(min=3)),
        Window(FormattedTextControl(count_text), height=1),
    ]))

    style = PTStyle.from_dict({
        "title": "bold #b39ddb reverse",
        "label": "bold #5fcde4",
        "selected": "bold #5fcde4",
        "item": "",
        "dim": "#888888",
    })

    try:
        app = Application(layout=layout, key_bindings=kb, style=style,
                          full_screen=True, mouse_support=False)
        return app.run()
    except Exception:
        return _numbered_model_select(models, current)


def _pick_model(agent: Agent, refresh: bool = False) -> None:
    """Open the interactive model picker, populated from the free-model list
    cached in .env (re-fetched from OpenRouter when missing or refresh=True).
    Persists the choice to .env (AGENT_MODEL) so it survives restarts. Shows
    the list read-only when running non-interactively."""
    if refresh:
        with ui.loading_dance("Refreshing free models from OpenRouter"):
            models = get_free_models(refresh=True)
    else:
        models = get_free_models()
    if not models:
        ui.print_error("No models available.")
        return

    # Non-interactive (no TTY / NO_QUESTIONS): just display the list.
    if os.environ.get("NO_QUESTIONS") == "1" or not sys.stdin.isatty():
        ui.print_model_picker(models, agent.model)
        return

    selected = _interactive_model_select(models, agent.model)
    if selected is None or selected == agent.model:
        ui.print_info("  Model unchanged.")
        return
    _apply_model(agent, selected)
    ui.print_settings_saved(selected)


def _handle_settings(rest: str, agent: Agent) -> None:
    """`/settings` — settings screen. Currently: model selection.

    `/settings refresh` re-fetches the free-model list from OpenRouter and
    re-caches it to .env; bare `/settings` (or `/settings model`) uses the cache.
    """
    state = "on" if findings.findings_enabled() else "off"
    ui.print_info(f"  Findings memory: {state}  [dim](toggle with /findings on|off)[/dim]")
    refresh = rest.strip().lower() in ("refresh", "model refresh", "reload")
    _pick_model(agent, refresh=refresh)


def _refresh_findings_prompt(agent: Agent) -> None:
    """Reload findings.md into the live system prompt after it changes on disk."""
    agent._findings = findings.load(agent.cwd)
    agent.set_mode(agent.mode)  # rebuild the system prompt with the new findings text


def _handle_log(rest: str, agent: Agent) -> None:
    """`/log [N]` — show the session-log path and its last N lines (default 30)."""
    if agent.session_log is None:
        ui.print_info("  Session logging is off (set AGENT_SESSION_LOG=1 to enable).")
        return
    path = agent.session_log.path
    ui.print_info(f"  Session log: {path}")
    try:
        n = int(rest.strip()) if rest.strip() else 30
    except ValueError:
        n = 30
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        ui.print_info("  (nothing logged yet)")
        return
    tail = "\n".join(lines[-n:])
    ui.console.print(tail)


def _handle_findings(rest: str, agent: Agent) -> None:
    """`/findings [list|on|off|clear]` — manage the persistent findings memory."""
    arg = rest.strip().lower()
    if arg in ("on", "off"):
        findings.set_enabled(arg == "on")
        _refresh_findings_prompt(agent)
        ui.print_info(f"  Findings memory turned {arg}.")
        return
    if arg == "clear":
        path = findings.findings_path(agent.cwd)
        if path.is_file():
            path.unlink()
        _refresh_findings_prompt(agent)
        ui.print_info("  Findings memory cleared.")
        return
    if arg and arg != "list":
        ui.print_error("Usage: /findings [list|on|off|clear]")
        return
    # Bare /findings or /findings list → show current state and contents.
    state = "on" if findings.findings_enabled() else "off"
    text = findings.load(agent.cwd)
    ui.print_info(f"  Findings memory: {state}  [dim]({findings.findings_path(agent.cwd)})[/dim]")
    if text:
        ui.console.print(text)
    else:
        ui.print_info("  No findings recorded yet.")


# ── Slash-command dispatcher ───────────────────────────────────────────────

def handle_slash_command(raw: str, agent: Agent, ctx: ProjectContext,
                         routines: RoutineManager,
                         mcp: "MCPManager | None" = None) -> bool:
    parts   = raw.strip().split(None, 1)
    command = parts[0].lower()
    rest    = parts[1] if len(parts) > 1 else ""

    # ── help ────────────────────────────────────────────────────────────────
    if command == "/help":
        ui.print_help(ctx.skills, ctx.agent_skills)
        return True

    # ── clear / new / reset ─────────────────────────────────────────────────
    if command in ("/clear", "/new", "/reset"):
        agent.clear()
        _sync_ctx(ctx, agent)
        return True

    # ── compact ─────────────────────────────────────────────────────────────
    if command == "/compact":
        ui.print_info("  Compacting conversation…")
        summary, count = agent.compact()
        if count:
            ui.print_compact_result(count, summary)
        else:
            ui.print_info("  Nothing to compact.")
        return True

    # ── plan / agent / go ───────────────────────────────────────────────────
    if command == "/plan":
        agent.set_mode("plan")
        ui.print_mode_change("plan")
        if rest:
            ui.print_user(rest)
            agent.run_turn(rest)
        return True

    if command == "/agent":
        agent.set_mode("agent")
        ui.print_mode_change("agent")
        return True

    if command == "/go":
        if agent.mode == "plan":
            agent.set_mode("agent")
            ui.print_mode_change("agent")
        ui.print_user("Execute the plan you just described.")
        agent.run_turn("Execute the plan you just described. Make all the changes now.")
        return True

    # ── exit / quit ─────────────────────────────────────────────────────────
    if command in ("/exit", "/quit"):
        raise _QuitSignal()

    # ── mcp — list MCP servers and tools ────────────────────────────────────
    if command == "/mcp":
        _handle_mcp(mcp)
        return True

    # ── cwd ─────────────────────────────────────────────────────────────────
    if command == "/cwd":
        if not rest:
            ui.print_info(f"Working directory: {agent.cwd}")
        else:
            new = os.path.abspath(os.path.expanduser(rest.strip()))
            if os.path.isdir(new):
                agent.cwd = new
                _sync_ctx(ctx, agent)
                ui.print_info(f"Working directory: {agent.cwd}")
            else:
                ui.print_error(f"Directory not found: {new}")
        return True

    # ── loop ────────────────────────────────────────────────────────────────
    if command == "/loop":
        handle_loop(rest, agent, ctx, routines)
        return True

    # ── btw — side question, no history ─────────────────────────────────────
    if command == "/btw":
        if not rest:
            ui.print_error("Usage: /btw <question>")
            return True
        ui.print_info("  Asking side question…")
        answer = agent.btw(rest)
        ui.print_btw_result(rest, answer)
        return True

    # ── settings — interactive settings screen ───────────────────────────────
    if command == "/settings":
        _handle_settings(rest, agent)
        return True

    # ── findings — view / clear / toggle the persistent findings memory ──────
    if command == "/findings":
        _handle_findings(rest, agent)
        return True

    # ── log — show the session transcript path and recent lines ──────────────
    if command == "/log":
        _handle_log(rest, agent)
        return True

    # ── model — open picker, or switch directly with an explicit id ──────────
    if command == "/model":
        if not rest or rest.strip().lower() in ("refresh", "reload"):
            # No argument → picker from .env cache; `refresh` → re-fetch list.
            _pick_model(agent, refresh=bool(rest))
        else:
            # Explicit id (e.g. /model anthropic/claude-opus-4-8) — validate,
            # then switch and persist.
            _switch_model_by_id(agent, rest)
        return True

    # ── recap — one-line session summary ────────────────────────────────────
    if command == "/recap":
        ui.print_info("  Generating recap…")
        summary = agent.recap()
        ui.print_btw_result("Session recap", summary)
        return True

    # ── skills — list all skills ─────────────────────────────────────────────
    if command == "/skills":
        ui.print_skills_list(ctx.skills, ctx.agent_skills)
        return True

    # ── reload-skills — rescan skills from disk ──────────────────────────────
    if command == "/reload-skills":
        new_ctx = load_context(agent.cwd)
        ctx.instructions = new_ctx.instructions
        ctx.fingerprint = new_ctx.fingerprint
        ctx.skills.clear()
        ctx.skills.update(new_ctx.skills)
        ctx.agent_skills.clear()
        ctx.agent_skills.update(new_ctx.agent_skills)
        # propagate to agent system prompt
        agent._extra        = ctx.instructions
        agent._agent_skills = ctx.agent_skills
        agent.set_mode(agent.mode)  # rebuild system prompt with updated instructions/skills
        ui.print_info(
            f"  Reloaded — {len(ctx.skills)} command(s), "
            f"{len(ctx.agent_skills)} agent skill(s)."
        )
        return True

    # ── rename — label the session ───────────────────────────────────────────
    if command == "/rename":
        if not rest:
            if agent.session_name:
                ui.print_info(f"  Session name: {agent.session_name!r}")
            else:
                ui.print_info("  Session has no name. Usage: /rename <name>")
        else:
            agent.session_name = rest.strip()
            ui.print_info(f"  Session renamed to: {agent.session_name!r}")
        return True

    # ── export — write conversation to file ──────────────────────────────────
    if command == "/export":
        text = _export_conversation(agent)
        if not text:
            ui.print_info("  Nothing to export.")
            return True
        if rest:
            path = os.path.join(agent.cwd, rest.strip())
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                ui.print_info(f"  Exported to: {path}")
            except OSError as e:
                ui.print_error(f"Export failed: {e}")
        else:
            if _copy_to_clipboard(text):
                ui.print_info("  Conversation copied to clipboard.")
            else:
                ui.print_error("Clipboard not available — pass a filename: /export <file.md>")
        return True

    # ── copy — copy last response to clipboard ───────────────────────────────
    if command == "/copy":
        if not agent._last_response:
            ui.print_info("  No response to copy yet.")
            return True
        if _copy_to_clipboard(agent._last_response):
            preview = agent._last_response[:80].replace("\n", " ")
            ui.print_info(f"  Copied to clipboard: {preview!r}…")
        else:
            ui.print_error(
                "Clipboard not available on this system. "
                "Use /export <file.md> to save the conversation instead."
            )
        return True

    # ── usage / cost — show token stats ──────────────────────────────────────
    if command in ("/usage", "/cost", "/stats"):
        ui.print_usage(agent)
        return True

    # ── context — show context window breakdown ───────────────────────────
    if command == "/context":
        ui.print_context(agent)
        return True

    # ── goal — set autonomous goal ───────────────────────────────────────────
    if command == "/goal":
        lower = rest.strip().lower()
        if not rest:
            if agent.goal:
                ui.print_info(f"  Active goal: {agent.goal!r}")
            else:
                ui.print_info("  No active goal. Usage: /goal <condition>")
        elif lower in ("clear", "stop", "off", "cancel", "none", "reset"):
            if agent.goal:
                ui.print_info(f"  Goal cleared: {agent.goal!r}")
                agent.goal = None
            else:
                ui.print_info("  No active goal.")
        else:
            agent.goal = rest.strip()
            ui.print_info(
                f"  Goal set: {agent.goal!r}\n"
                "  GUS will keep working after each turn until the goal is met.\n"
                "  Press Ctrl+C to interrupt, or /goal clear to cancel."
            )
        return True

    # ── registered custom commands ──────────────────────────────────────────
    name = command.lstrip("/")
    if name in ctx.skills:
        cmd = ctx.skills[name]
        if cmd.confirm and not _confirm(f"  Run /{name}?"):
            ui.print_info("Cancelled.")
            return True
        if cmd.max_iterations > 1:
            _run_fixed_loop(cmd, rest, agent, cmd.max_iterations, ctx)
        else:
            _run_command(cmd, rest, agent)
        return True

    # ── Agent Skills (agentskills.io) ───────────────────────────────────────
    if name in ctx.agent_skills:
        skill = ctx.agent_skills[name]
        prompt = skill.body + (f"\n\n{rest}" if rest else "")
        ui.print_user(prompt)
        agent.run_turn(prompt)
        return True

    return False


# ── Context sync ──────────────────────────────────────────────────────────

def _sync_ctx(ctx: ProjectContext, agent: Agent) -> None:
    """Reload skills/commands from disk into ctx. Called after each turn.

    Skips the full disk scan + system-prompt rebuild when nothing on disk
    changed (the common case), so steady-state turns pay only a cheap stat walk.
    """
    fp = context_fingerprint(agent.cwd)
    if fp == ctx.fingerprint and agent._extra == ctx.instructions:
        return
    new = load_context(agent.cwd)
    ctx.fingerprint = new.fingerprint
    added_cmds   = [k for k in new.skills       if k not in ctx.skills]
    added_skills = [k for k in new.agent_skills if k not in ctx.agent_skills]
    ctx.skills.clear()
    ctx.skills.update(new.skills)
    ctx.agent_skills.clear()
    ctx.agent_skills.update(new.agent_skills)
    agent._extra        = new.instructions
    agent._agent_skills = new.agent_skills
    agent.set_mode(agent.mode)
    if added_cmds:
        ui.print_info("  New command(s) available: " + ", ".join("/" + k for k in added_cmds))
    if added_skills:
        ui.print_info("  New skill(s) available: " + ", ".join("/" + k for k in added_skills))
    for warn in new.skill_warnings:
        ui.print_warning(f"  Skill spec violation: {warn}")


# ── Terminal resize handling ───────────────────────────────────────────────

def _install_resize_handler():
    """Install a SIGWINCH handler that repairs Rich Live output on resize.

    Scoped to run only while GUS is rendering (an agent turn), so prompt_toolkit
    keeps full ownership of SIGWINCH at the idle prompt. Returns the previous
    handler to restore afterwards, or None when unavailable (Windows, or called
    off the main thread — signal.signal raises there).
    """
    if not hasattr(signal, "SIGWINCH"):
        return None

    def _on_winch(signum, frame):
        try:
            ui.handle_resize()
        except Exception:
            pass

    try:
        return signal.signal(signal.SIGWINCH, _on_winch)
    except (ValueError, OSError):
        return None


def _restore_resize_handler(prev) -> None:
    if prev is None or not hasattr(signal, "SIGWINCH"):
        return
    try:
        signal.signal(signal.SIGWINCH, prev)
    except (ValueError, OSError):
        pass


# ── REPL ───────────────────────────────────────────────────────────────────

def run_interactive(agent: Agent, ctx: ProjectContext,
                    mcp: "MCPManager | None" = None) -> None:
    routines = RoutineManager(agent)
    session: PromptSession = PromptSession(
        history=FileHistory(HISTORY_FILE),
        auto_suggest=AutoSuggestFromHistory(),
        completer=_GusCompleter(ctx),
        complete_while_typing=True,
        style=PROMPT_STYLE,
    )
    ui.print_hello_splash()
    ui.print_banner(DEFAULT_MODEL, agent.cwd, ctx)

    while True:
        # fire every-turn hooks before each prompt
        if routines.every_turn:
            routines.run_every_turn_hooks()

        prompt_str = "\n[plan] > " if agent.mode == "plan" else "\n> "
        toolbar    = ui.get_bottom_toolbar(agent, routines)
        try:
            user_input = session.prompt(
                [("class:prompt", prompt_str)],
                bottom_toolbar=toolbar,
            ).strip()
        except EOFError:
            break  # Ctrl+D quits
        except KeyboardInterrupt:
            # Ctrl+C never quits GUS (use Ctrl+D or /exit). At the idle prompt
            # the main thread can't see background routines running in daemon
            # threads, so a Ctrl+C here is the user trying to stop them: signal
            # the shared interrupt flag (any in-flight tool polls it and bails)
            # and tear the routines down. With nothing running, just cancel the
            # current input line like a shell.
            active = routines.list_all()
            if active:
                set_interrupt()
                routines.stop_all()
                ui.console.print(
                    f"\n[dim]*Stopped {len(active)} routine(s). "
                    "Ctrl+D or /exit to quit GUS.*[/dim]"
                )
            else:
                ui.console.print(
                    "[dim]  (Ctrl+C cancels input — Ctrl+D or /exit to quit)[/dim]"
                )
            continue

        if not user_input:
            continue

        # Take over SIGWINCH while we render so a window resize mid-output
        # repairs the Rich Live region instead of corrupting it. Restored in
        # the finally so prompt_toolkit owns resizes again at the idle prompt.
        prev_winch = _install_resize_handler()
        try:
            if user_input.startswith("/"):
                try:
                    if not handle_slash_command(user_input, agent, ctx, routines, mcp=mcp):
                        ui.print_error(
                            f"Unknown command: {user_input.split()[0]}. Type /help."
                        )
                except _QuitSignal:
                    break  # fall through to cleanup
                continue

            ui.print_user(user_input)
            try:
                start = len(agent.history)
                with routines.acquire():
                    agent.run_turn(user_input)
                if agent.goal and not is_interrupted():
                    _run_goal_loop(agent, routines)
                # Persist what was learned this turn (best-effort, never raises).
                findings.persist_turn(agent, agent.history[start:], user_input)
            except KeyboardInterrupt:
                ui.console.print("\n[dim]*interrupted — back to prompt (Ctrl+D or /exit to quit)*[/dim]")
            except AuthenticationError:
                if not _prompt_replace_key(agent):
                    break  # user chose to exit
                ui.console.print("[dim]  Key updated — please re-send your message.[/dim]")
            except Exception as e:
                ui.print_confused(str(e))
            else:
                _sync_ctx(ctx, agent)
        finally:
            _restore_resize_handler(prev_winch)

    # ── clean exit ─────────────────────────────────────────────────────────
    routines.stop_all()
    ui.print_flying_away()


# ── One-shot ───────────────────────────────────────────────────────────────

def run_oneshot(agent: Agent, prompt: str) -> None:
    _install_resize_handler()  # no prompt_toolkit here, so keep it for the run
    start = len(agent.history)
    try:
        agent.run_turn(prompt)
    except AuthenticationError:
        if _prompt_replace_key(agent):
            # retry once with the new key
            try:
                agent.run_turn(prompt)
            except Exception as e:
                ui.print_confused(str(e))
                sys.exit(1)
        else:
            sys.exit(1)
    except Exception as e:
        ui.print_confused(str(e))
        sys.exit(1)
    # Persist what was learned this run (best-effort, never raises).
    findings.persist_turn(agent, agent.history[start:], prompt)


# ── Key management ────────────────────────────────────────────────────────

def _save_key_to_env(key: str) -> None:
    """Write/replace OPENROUTER_API_KEY in .env and update the live process env."""
    save_env_var("OPENROUTER_API_KEY", key)


def _prompt_replace_key(agent: Agent | None = None) -> bool:
    """
    Show an invalid-key panel, ask the user to replace or abort.
    Updates .env, os.environ, and agent.client if a new valid key is given.
    Returns True if the key was replaced, False if the user chose to exit.
    """
    from rich.panel import Panel
    from rich import box as _box
    ui.console.print(
        Panel(
            "[bold red]API key rejected (401 Unauthorised)[/bold red]\n\n"
            "Possible causes:\n"
            "  • The key was revoked or never activated\n"
            "  • You pasted it with extra spaces or characters\n"
            "  • The key belongs to a different provider\n\n"
            "You can get or regenerate a key at:\n"
            "  [cyan underline]https://openrouter.ai[/cyan underline]  "
            "→ avatar → [bold]API Keys[/bold]",
            title="[bold red]🦆 GUS — Invalid Key[/bold red]",
            border_style="red",
            box=_box.ROUNDED,
        )
    )
    ui.console.print("  [dim]Press Enter with no input to exit.[/dim]")

    while True:
        try:
            key = input("\n  New API key (or Enter to exit): ").strip()
        except (KeyboardInterrupt, EOFError):
            return False

        if not key:
            return False
        if not key.startswith("sk-"):
            ui.console.print("[dim]  Doesn't look right (should start with sk-). Try again.[/dim]")
            continue
        break

    _save_key_to_env(key)
    if agent is not None:
        agent.client = get_client()
    ui.console.print("[bold green]  ✓ Key updated — retry your message.[/bold green]\n")
    return True


# ── First-run setup ────────────────────────────────────────────────────────

def _ensure_env() -> None:
    """
    If .env is missing or has no OPENROUTER_API_KEY, walk the user through
    creating one before the rest of the app starts.
    """
    existing_key = ""
    if os.path.isfile(_ENV_FILE):
        with open(_ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    existing_key = line.split("=", 1)[1].strip().strip('"').strip("'")

    if existing_key:
        return  # already configured

    from rich.panel import Panel
    from rich import box as _box
    ui.console.print(
        Panel(
            "[bold yellow]Welcome to GUS! 🦆[/bold yellow]\n\n"
            "No API key found. GUS needs an [cyan]OpenRouter[/cyan] key to talk to AI models.\n\n"
            "[bold]How to get a free key:[/bold]\n"
            "  1. Go to [cyan underline]https://openrouter.ai[/cyan underline]\n"
            "  2. Sign up (free) or log in\n"
            "  3. Click your avatar → [bold]API Keys[/bold]\n"
            "  4. Press [bold]Create key[/bold] and copy it\n\n"
            "[dim]Free models (no credits needed) are available immediately.[/dim]",
            title="[bold yellow]🦆 GUS — First Run Setup[/bold yellow]",
            border_style="yellow",
            box=_box.ROUNDED,
        )
    )

    while True:
        try:
            key = input("\n  Paste your OpenRouter API key: ").strip()
        except (KeyboardInterrupt, EOFError):
            ui.console.print("\n[dim]Setup cancelled.[/dim]")
            sys.exit(0)

        if not key:
            ui.console.print("[dim]  Key cannot be empty — try again.[/dim]")
            continue
        if not key.startswith("sk-"):
            ui.console.print("[dim]  That doesn't look right (should start with sk-). Try again.[/dim]")
            continue
        break

    _save_key_to_env(key)

    ui.console.print("[bold green]  ✓ Key saved to .env — you're all set![/bold green]\n")

    # Pre-fill the free-model list into .env so the picker is ready offline.
    with ui.loading_dance("Fetching free models from OpenRouter"):
        models = fetch_free_models()
    if models:
        save_free_models(models)
        ui.console.print(
            f"[bold green]  ✓ Loaded {len(models)} free models into .env "
            "— pick one any time with /settings.[/bold green]\n"
        )


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    _ensure_env()
    args = parse_args()

    try:
        client = get_client()
    except ValueError as e:
        ui.print_error(str(e))
        sys.exit(1)

    cwd   = os.path.abspath(os.path.expanduser(args.cwd))
    _ensure_gus_dirs(cwd)
    ctx   = load_context(cwd)
    findings_text = findings.load(cwd)
    agent = Agent(client=client, model=DEFAULT_MODEL, cwd=cwd,
                  extra_instructions=ctx.instructions,
                  agent_skills=ctx.agent_skills,
                  findings_text=findings_text,
                  enable_session_log=True)

    if ctx.instructions:
        ui.print_info(f"  Loaded agents.md ({len(ctx.instructions)} chars)")
    if findings_text:
        ui.print_info(f"  Loaded findings memory ({len(findings_text)} chars)")
    if agent.session_log:
        try:
            rel = os.path.relpath(agent.session_log.path, cwd)
        except ValueError:
            rel = str(agent.session_log.path)
        ui.print_info(f"  Session log: {rel}")
    if ctx.skills:
        ui.print_info(f"  Loaded {len(ctx.skills)} command(s): "
                      + ", ".join("/" + s for s in ctx.skills))
    if ctx.agent_skills:
        ui.print_info(f"  Loaded {len(ctx.agent_skills)} agent skill(s): "
                      + ", ".join("/" + s for s in ctx.agent_skills))
    for warn in ctx.skill_warnings:
        ui.print_warning(f"  Skill spec violation: {warn}")

    mcp = MCPManager(cwd)
    with ui.loading_dance("Starting MCP servers"):
        n_servers = mcp.start_all()
    if n_servers:
        n_tools = register_mcp_tools(mcp)
        ui.print_info(f"  MCP: {n_servers} server(s), {n_tools} tool(s) available")

    try:
        if args.prompt:
            run_oneshot(agent, args.prompt)
        else:
            run_interactive(agent, ctx, mcp=mcp)
    finally:
        mcp.stop_all()


if __name__ == "__main__":
    main()
