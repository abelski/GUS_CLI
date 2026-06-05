#!/usr/bin/env python3
"""GUS — CLI agent entry point."""
import os
import platform
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))

import argparse

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style

from pathlib import Path

from openai import AuthenticationError

import ui
from config import get_client, DEFAULT_MODEL, WORKING_DIR, CONFIG_DIR
from agent import Agent
from context import load_context, ProjectContext, Command
from loop import RoutineManager, parse_interval, interval_label
from mcp_client import MCPManager
from tools import register_mcp_tools

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
    ui.print_user(prompt)
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
    while agent.goal:
        ui.print_info(f"  Checking goal: {agent.goal!r}")
        try:
            satisfied = _check_goal_satisfied(agent)
        except Exception:
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


# ── Clipboard helper ───────────────────────────────────────────────────────

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

    # ── model — switch or show model ─────────────────────────────────────────
    if command == "/model":
        if not rest:
            ui.print_info(f"  Current model: {agent.model}")
        else:
            agent.model = rest.strip()
            ui.print_info(f"  Model switched to: {agent.model}")
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
        if cmd.confirm:
            answer = input(f"  Run /{name}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
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
    """Reload skills/commands from disk into ctx. Called after each turn."""
    new = load_context(agent.cwd)
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


# ── REPL ───────────────────────────────────────────────────────────────────

def run_interactive(agent: Agent, ctx: ProjectContext,
                    mcp: "MCPManager | None" = None) -> None:
    routines = RoutineManager(agent)
    session: PromptSession = PromptSession(
        history=FileHistory(HISTORY_FILE),
        auto_suggest=AutoSuggestFromHistory(),
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
        except (KeyboardInterrupt, EOFError):
            try:
                confirm = session.prompt(
                    [("class:prompt", "\nExit GUS? [y/N] ")]
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                confirm = "y"
            if confirm in ("y", "yes"):
                break
            continue

        if not user_input:
            continue

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
            with routines.acquire():
                agent.run_turn(user_input)
            if agent.goal:
                _run_goal_loop(agent, routines)
        except KeyboardInterrupt:
            ui.console.print("\n[dim]*interrupted — /exit to quit*[/dim]")
        except AuthenticationError:
            if not _prompt_replace_key(agent):
                break  # user chose to exit
            ui.console.print("[dim]  Key updated — please re-send your message.[/dim]")
        except Exception as e:
            ui.print_confused(str(e))
        else:
            _sync_ctx(ctx, agent)

    # ── clean exit ─────────────────────────────────────────────────────────
    routines.stop_all()
    ui.print_flying_away()


# ── One-shot ───────────────────────────────────────────────────────────────

def run_oneshot(agent: Agent, prompt: str) -> None:
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


# ── Key management ────────────────────────────────────────────────────────

def _save_key_to_env(key: str) -> None:
    """Write/replace OPENROUTER_API_KEY in .env and update the live process env."""
    lines: list[str] = []
    if os.path.isfile(_ENV_FILE):
        with open(_ENV_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    # Replace existing line or append
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("OPENROUTER_API_KEY="):
            lines[i] = f"OPENROUTER_API_KEY={key}\n"
            replaced = True
            break
    if not replaced:
        lines.append(f"OPENROUTER_API_KEY={key}\n")
    with open(_ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.environ["OPENROUTER_API_KEY"] = key


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
    agent = Agent(client=client, model=DEFAULT_MODEL, cwd=cwd,
                  extra_instructions=ctx.instructions,
                  agent_skills=ctx.agent_skills)

    if ctx.instructions:
        ui.print_info(f"  Loaded agents.md ({len(ctx.instructions)} chars)")
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
