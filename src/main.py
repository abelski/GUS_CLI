#!/usr/bin/env python3
"""GUS — CLI agent entry point."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import argparse

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style

from openai import AuthenticationError

import ui
from config import get_client, DEFAULT_MODEL, WORKING_DIR
from agent import Agent
from context import load_context, ProjectContext, Command
from loop import RoutineManager, parse_interval, interval_label

# Project root is one level above src/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_FILE     = os.path.join(_PROJECT_ROOT, ".env")


class _QuitSignal(Exception):
    """Raised by /exit or /quit to break out of the REPL loop cleanly."""


HISTORY_FILE = os.path.expanduser("~/.gus_history")
PROMPT_STYLE = Style.from_dict({"prompt": "bold ansicyan"})


# ── Argument parsing ───────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GUS — AI coding assistant powered by OpenRouter",
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
        # For timed routines with shell commands, build the full prompt now
        # and let the routine re-run the shell each time via agent.run_turn
        final_prompt = prompt_text
        if cmd_obj and cmd_obj.shell:
            # wrap the command name so the routine re-executes shell each time
            # we store a lambda-like closure prompt that does the full thing
            # simplest: store a special marker and handle in routine body
            # easier: pre-bake the current shell output — but that won't refresh
            # best: store as Command and rebuild each fire → use a subclass-free approach
            final_prompt = f"__cmd__{cmd_obj.name}__{cmd_args}"
        r = routines.add_timed(final_prompt, interval)
        if cmd_obj:
            # override with a proper runner in the thread body
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


# ── Slash-command dispatcher ───────────────────────────────────────────────

def handle_slash_command(raw: str, agent: Agent, ctx: ProjectContext,
                         routines: RoutineManager) -> bool:
    parts   = raw.strip().split(None, 1)
    command = parts[0].lower()
    rest    = parts[1] if len(parts) > 1 else ""

    if command == "/help":
        ui.print_help(ctx.skills)
        return True

    if command == "/clear":
        agent.clear()
        return True

    if command == "/compact":
        ui.print_info("  Compacting conversation…")
        summary, count = agent.compact()
        if count:
            ui.print_compact_result(count, summary)
        else:
            ui.print_info("  Nothing to compact.")
        return True

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

    if command in ("/exit", "/quit"):
        raise _QuitSignal()

    if command == "/cwd":
        if not rest:
            ui.print_info(f"Working directory: {agent.cwd}")
        else:
            new = os.path.abspath(os.path.expanduser(rest.strip()))
            if os.path.isdir(new):
                agent.cwd = new
                ui.print_info(f"Working directory: {agent.cwd}")
            else:
                ui.print_error(f"Directory not found: {new}")
        return True

    if command == "/loop":
        handle_loop(rest, agent, ctx, routines)
        return True

    # registered commands
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

    return False


# ── REPL ───────────────────────────────────────────────────────────────────

def run_interactive(agent: Agent, ctx: ProjectContext) -> None:
    routines = RoutineManager(agent)
    session: PromptSession = PromptSession(
        history=FileHistory(HISTORY_FILE),
        auto_suggest=AutoSuggestFromHistory(),
        style=PROMPT_STYLE,
    )
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
            break  # Ctrl+C / Ctrl+D at the blank prompt → clean exit

        if not user_input:
            continue

        if user_input.startswith("/"):
            try:
                if not handle_slash_command(user_input, agent, ctx, routines):
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
        except KeyboardInterrupt:
            ui.console.print("\n[dim]*interrupted — /exit to quit*[/dim]")
        except AuthenticationError:
            if not _prompt_replace_key(agent):
                break  # user chose to exit
            ui.console.print("[dim]  Key updated — please re-send your message.[/dim]")
        except Exception as e:
            ui.print_confused(str(e))

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
    # Read existing .env (may not exist)
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
    ctx   = load_context(cwd)
    agent = Agent(client=client, model=DEFAULT_MODEL, cwd=cwd,
                  extra_instructions=ctx.instructions)

    if ctx.instructions:
        ui.print_info(f"  Loaded agents.md ({len(ctx.instructions)} chars)")
    if ctx.skills:
        ui.print_info(f"  Loaded {len(ctx.skills)} command(s): "
                      + ", ".join("/" + s for s in ctx.skills))

    if args.prompt:
        run_oneshot(agent, args.prompt)
    else:
        run_interactive(agent, ctx)


if __name__ == "__main__":
    main()
