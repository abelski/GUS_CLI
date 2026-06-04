"""Terminal UI — GUS agent personality and rendering."""
import getpass
import json
import random
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich import box

# highlight=False: let Markdown handle its own colouring; prevents Rich's
# auto-highlighter from recolouring table cell content mid-render.
console = Console(highlight=False)


def _md(text: str) -> Padding:
    """Render markdown with a consistent code theme, left-indented for readability."""
    return Padding(
        Markdown(text, code_theme="monokai", hyperlinks=False),
        pad=(0, 0, 0, 2),
    )

# ── Thinking spinner ───────────────────────────────────────────────────────

_active_status: Status | None = None

_SPINNER_PHRASES = [
    "🦆 *squints intensely*",
    "🦆 *aggressive eye contact*",
    "🦆 *waddling circles around the problem*",
    "🦆 *ruffles feathers thoughtfully*",
    "🦆 *stares until the code confesses*",
    "🦆 *tilts head 47 degrees*",
    "🦆 *paces back and forth*",
    "🦆 *consulting the pond oracle*",
]


def thinking_start() -> None:
    """Show animated spinner while waiting for first LLM token."""
    global _active_status
    phrase = random.choice(_SPINNER_PHRASES)
    _active_status = console.status(
        f"[dim italic]{phrase}[/dim italic]",
        spinner="dots",
        spinner_style="yellow",
    )
    _active_status.start()


def thinking_stop() -> None:
    """Stop and clear the spinner."""
    global _active_status
    if _active_status is not None:
        _active_status.stop()
        _active_status = None


# ── Duck mood ASCII art ────────────────────────────────────────────────────

def _duck(mood: str = "normal") -> Text:
    """
    Render the GUS mascot in one of several moods.
    Uses Rich Text objects so backslashes are always literal characters.
    """
    F     = "bold yellow"                  # solid fill blocks: ▄ ▀ █
    FB    = "on yellow"                    # yellow bg for interior spaces
    EY    = "bold black on yellow"         # normal eyes
    BL    = "bold dark_orange on yellow"   # bill / beak detail
    RED   = "bold red on yellow"           # staring / confused accents
    GRN   = "bold green on yellow"         # celebrating
    TILDE = "bold cyan"                    # water / wind waves (outside body)

    def row(*parts) -> Text:
        t = Text()
        for txt, style in parts:
            t.append(txt, style=style)
        return t

    # Shared structural pieces (interior = 13 chars between █ edges)
    TOP = ("   ▄▄▄▄▄▄▄▄▄▄▄▄▄ ", F)
    BOT = ("   ▀▀▀▀▀▀▀▀▀▀▀▀▀ ", F)
    LE  = ("  █", F)
    RE  = ("█",  F)

    moods: dict[str, list] = {

        "normal": [
            row(TOP),
            row(LE, ("  ", FB), ("◉", EY), ("     ", FB), ("◉", EY), ("    ", FB), RE),
            row(LE, ("  ", FB), ("▬▬▬▬▬", BL), ("      ", FB), RE),
            row(LE, ("             ", FB), RE),
            row(BOT),
        ],

        "staring": [
            row(TOP),
            row(LE, ("  ", FB), ("◉", RED), ("     ", FB), ("◉", RED), ("    ", FB), RE),
            row(LE, ("  ", FB), (">:===:<", RED), ("    ", FB), RE),
            row(LE, ("             ", FB), RE),
            row(BOT),
        ],

        "gusing": [
            row(TOP),
            row(LE, ("  ", FB), ("◉", EY), ("     ", FB), ("◉", EY), ("    ", FB), RE),
            row(LE, ("   ", FB), ("=GUS!=", BL), ("    ", FB), RE),
            row(LE, ("  *   *   *  ", BL), RE),
            row(BOT),
        ],

        "celebrating": [
            row(TOP),
            row(LE, ("  ", FB), ("^", GRN), ("     ", FB), ("^", GRN), ("    ", FB), RE),
            row(LE, ("    ", FB), ("~~~~~", GRN), ("    ", FB), RE),
            row(LE, (" * HOORAY *  ", GRN), RE),
            row(BOT),
        ],

        "confused": [
            row(TOP),
            row(LE, ("  ", FB), ("◉", EY), ("     ", FB), ("?", RED), ("    ", FB), RE),
            row(LE, ("  ", FB), ("====?", BL), ("      ", FB), RE),
            row(LE, ("  ???   ???  ", RED), RE),
            row(BOT),
        ],

        "flying": [
            row(("   ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄", F)),
            row(("~~~", TILDE), ("█", F), ("  ", FB), ("o", EY), ("     ", FB), ("o", EY), ("    ", FB), ("█", F), ("~~~", TILDE)),
            row(("~~(", TILDE), ("█", F), ("  ", FB), ("flap flap", "bold black on yellow"), ("  ", FB), ("█", F), (")~~", TILDE)),
            row(("~~~", TILDE), ("█", F), ("    ", FB), ("~~~~", "bold cyan on yellow"), ("     ", FB), ("█", F), ("~~~", TILDE)),
            row(("   ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀", F)),
        ],

        "diving": [
            row(BOT),
            row(LE, ("             ", FB), RE),
            row(LE, ("  ", FB), ("▬▬▬▬▬", BL), ("      ", FB), RE),
            row(LE, ("  ", FB), ("◉", EY), ("     ", FB), ("◉", EY), ("    ", FB), RE),
            row(TOP),
            row(("  ~~~~~~~~~~~~~~~", TILDE)),
        ],

        "sleeping": [
            row(TOP),
            row(LE, ("  ", FB), ("─", EY), ("     ", FB), ("─", EY), ("    ", FB), RE),
            row(LE, ("  ", FB), ("▬▬▬▬▬", BL), ("      ", FB), RE),
            row(LE, ("  ", FB), ("z z z", "dim white on yellow"), ("      ", FB), RE),
            row(BOT),
        ],

    }

    rows = moods.get(mood, moods["normal"])
    result = Text()
    for i, r in enumerate(rows):
        result.append_text(r)
        if i < len(rows) - 1:
            result.append("\n")
    return result


# ── Duck personality phrases ───────────────────────────────────────────────

_DONE = [
    "QUAAAAACK! 🦆",
    "*triumphant wing flap*",
    "*proud duck noises*",
    "GUS gus — nailed it.",
    "*does a little celebratory waddle*",
    "GUS! Done. *shakes tail feathers*",
]

_ERRORS = [
    "*confused gusing*",
    "GUS?? ...gus.",
    "*tilts head, tilts it further, falls over*",
    "*aggressively stares at error*",
    "*ruffles feathers in frustration*",
]

# Tool-specific duck flavour: (emoji, action phrase)
_TOOL_FLAVOR: dict[str, tuple[str, str]] = {
    "bash":       ("🦆", random.choice(["*flapping wings*",       "*splashing around*",      "*waddling through shell*"])),
    "web_search": ("🌊", random.choice(["*diving into the web*",   "*paddling upstream*",     "*searching the pond*"])),
    "read_file":  ("👁️",  random.choice(["*squinting at file*",    "*peering closely*",       "*reading with one eye*"])),
    "write_file": ("✍️",  random.choice(["*writing with beak*",    "*pecking keys*",          "*scribing furiously*"])),
    "edit_file":  ("✂️",  random.choice(["*precise beak surgery*", "*nibbling at the code*",  "*careful pecking*"])),
    "glob":       ("🔍", random.choice(["*sniffing around dirs*",  "*waddling through paths*","*scouting the pond*"])),
    "grep":       ("👃", random.choice(["*sniffing for pattern*",  "*tracking the scent*",    "*hunting through files*"])),
    "list_dir":   ("👀", random.choice(["*peeking inside*",        "*craning neck*",          "*nosy duck is nosy*"])),
    "monitor":      ("🔭", random.choice(["*staring intensely*",     "*standing very still*",   "*refusing to blink*"])),
    "spawn_agent":  ("🤖", random.choice(["*summoning a colleague*", "*delegating with authority*", "*cloning self*"])),
    "web_fetch":    ("🌐", random.choice(["*paddling to the URL*",   "*fetching from the pond*", "*reading the web*"])),
    "todo_write":   ("📋", random.choice(["*updating the list*",     "*checking things off*",    "*organising tasks*"])),
    "task_create":  ("➕", random.choice(["*adding a task*",         "*quacking it down*",       "*noting it carefully*"])),
    "task_list":    ("📋", random.choice(["*checking the board*",    "*reviewing progress*",     "*scanning tasks*"])),
    "task_get":     ("🔎", random.choice(["*inspecting task*",       "*peering at details*",     "*reading the brief*"])),
    "task_update":  ("✏️",  random.choice(["*updating task*",        "*editing the board*",      "*marking progress*"])),
    "ask_user":     ("❓", random.choice(["*tilting head*",           "*needs clarification*",    "*peering at human*"])),
}


# ── Speaker labels ─────────────────────────────────────────────────────────

_live_render: Live | None = None
_live_text:   str = ""


def print_user(message: str) -> None:
    console.print(f"\n[bold cyan]You[/bold cyan]  {message}")


def print_assistant_start() -> None:
    global _live_render, _live_text
    console.print("\n[bold yellow]GUS[/bold yellow]")
    _live_text = ""
    _live_render = Live(
        _md(""),
        console=console,
        vertical_overflow="visible",
        refresh_per_second=8,
    )
    _live_render.start()


def print_assistant_chunk(text: str) -> None:
    global _live_text
    if _live_render is not None:
        _live_text += text
        _live_render.update(_md(_live_text))
    else:
        console.print(text, end="", markup=False)


def print_assistant_end() -> None:
    global _live_render, _live_text
    if _live_render is not None:
        # Final render with complete text, then stop
        _live_render.update(_md(_live_text))
        _live_render.stop()
        _live_render = None
        _live_text = ""
    else:
        console.print()


# ── Tool display ───────────────────────────────────────────────────────────

def print_tool_call(tool_name: str, args: dict) -> None:
    emoji, action = _TOOL_FLAVOR.get(tool_name, ("⚙️", tool_name))
    args_str = json.dumps(args, indent=2)
    console.print(f"\n[bold yellow]  {emoji} {tool_name}[/bold yellow]  [dim italic]{action}[/dim italic]",
                  highlight=False)
    if len(args_str) < 300:
        console.print(f"[dim]{args_str}[/dim]")


def print_tool_result(tool_name: str, result: str, error: bool = False) -> None:
    color        = "red" if error else "dim"
    symbol       = "✗" if error else "✓"
    symbol_color = "red" if error else "green"
    console.print(f"  [{symbol_color}]{symbol}[/{symbol_color}] [dim]{tool_name}[/dim]")
    if result and len(result) < 2000:
        console.print(f"[{color}]{result}[/{color}]")
    elif result:
        lines = result.split("\n")
        console.print(f"[{color}]{chr(10).join(lines[:20])}[/{color}]")
        console.print(f"[dim]  ... ({len(lines)} lines total)[/dim]")


# ── Duck event moments ─────────────────────────────────────────────────────

def print_gus_done() -> None:
    """After a completed turn — random celebration phrase."""
    console.print(f"\n[bold yellow]{random.choice(_DONE)}[/bold yellow]")


def print_celebrate() -> None:
    """Big celebration — full celebrating duck."""
    duck = _duck("celebrating")
    console.print(Panel(duck, border_style="green", box=box.HEAVY,
                        title="[bold green]🎉 GUS! 🎉[/bold green]"))


def print_flying_away() -> None:
    """Shown on Ctrl+C interrupt."""
    duck = _duck("flying")
    console.print(Panel(duck, border_style="cyan", box=box.ROUNDED,
                        title="[bold cyan]🦆 bye bye ...[/bold cyan]"))


def print_confused(message: str) -> None:
    """Shown on error — confused duck + message."""
    phrase = random.choice(_ERRORS)
    duck   = _duck("confused")
    body   = Text()
    body.append_text(duck)
    body.append(f"\n\n{phrase}\n")
    body.append(message, style="bold red")
    console.print(Panel(body, border_style="red", box=box.ROUNDED,
                        title="[bold red]gus?[/bold red]"))


def print_diving() -> None:
    """Shown briefly when web search starts."""
    duck = _duck("diving")
    console.print(Panel(duck, border_style="cyan", box=box.SIMPLE,
                        title="[bold cyan]🌊 searching...[/bold cyan]"))


# ── Compact ────────────────────────────────────────────────────────────────

def print_compact_result(old_count: int, summary: str) -> None:
    console.print(Panel(
        f"[dim]{summary}[/dim]",
        title=f"[bold cyan]🗜 Compacted {old_count} messages → 1 summary[/bold cyan]",
        border_style="cyan",
        box=box.SIMPLE,
    ))


# ── Sub-agent ──────────────────────────────────────────────────────────────

_subagent_depth = 0

def print_subagent_start(task: str) -> None:
    global _subagent_depth
    _subagent_depth += 1
    console.rule(f"[bold magenta]🤖 Sub-agent #{_subagent_depth}: {task[:72]}[/bold magenta]",
                 style="magenta")


def print_subagent_end(failed: bool = False) -> None:
    global _subagent_depth
    label  = "[bold red]✗ Sub-agent failed[/bold red]" if failed else "[bold magenta]✓ Sub-agent done[/bold magenta]"
    console.rule(label, style="magenta dim")
    _subagent_depth = max(0, _subagent_depth - 1)


# ── Mode indicator ─────────────────────────────────────────────────────────

def print_mode_change(mode: str) -> None:
    if mode == "plan":
        console.print("[bold cyan]📋 Plan mode — GUS will analyse and plan, not execute.[/bold cyan]")
        console.print("[dim]  Use /go when ready to execute the plan.[/dim]")
    else:
        console.print("[bold yellow]⚡ Agent mode — GUS will execute directly.[/bold yellow]")


# ── Notifications ──────────────────────────────────────────────────────────

def print_error(message: str) -> None:
    console.print(f"\n[bold red]Error:[/bold red] {message}")


def print_info(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


# ── Banner ─────────────────────────────────────────────────────────────────

def print_banner(model: str, working_dir: str, ctx=None, mode: str = "agent") -> None:
    from config import VERSION

    raw_user = getpass.getuser()
    username = raw_user.replace("_", " ").replace("-", " ").title()

    # ── top info table ─────────────────────────────────────────────────────
    info = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    info.add_column(style="bold cyan",    min_width=12)
    info.add_column(style="white")
    info.add_row("CLI Version", VERSION)
    info.add_row("Model",       model)
    info.add_row("CWD",         working_dir)
    if mode == "plan":
        info.add_row("Mode", "[bold cyan]PLAN[/bold cyan]")
    if ctx and ctx.skills:
        info.add_row("Commands", "  ".join(f"/{s}" for s in ctx.skills))
    console.print(info)
    console.print()

    # ── left column: welcome + duck ────────────────────────────────────────
    left = Text(justify="center")
    left.append(f"Welcome back {username}!\n\n", style="bold yellow")
    left.append_text(_duck("normal"))
    left.append(f"\n\n{model}", style="cyan dim")
    left.append(f"\n{working_dir}", style="dim")
    if ctx and ctx.instructions:
        left.append("\nagents.md loaded", style="dim")

    # ── right column: tips + what's new ───────────────────────────────────
    right = Text(no_wrap=False, overflow="fold")
    right.append("Tips for getting started\n", style="bold yellow")
    right.append("  /plan <task>   plan before executing\n",  style="dim")
    right.append("  /go            execute the plan\n",       style="dim")
    right.append("  /compact       compress conversation\n",  style="dim")
    right.append("  /loop 1h …     schedule a routine\n",     style="dim")
    right.append("  /help          all commands\n",           style="dim")
    right.append("\nWhat's new\n", style="bold yellow")
    right.append("  spawn_agent tool for sub-tasks\n",        style="dim")
    right.append("  /plan + /go mode for safer edits\n",      style="dim")
    right.append("  Time-based routines (1h, 1d…)\n",         style="dim")
    right.append("  /compact to save context\n",              style="dim")
    right.append("  First-run API key setup\n",               style="dim")

    # ── two-column grid with divider ───────────────────────────────────────
    grid = Table(
        box=box.MINIMAL,
        show_header=False,
        expand=True,
        show_edge=False,
        padding=(0, 2),
        border_style="yellow dim",
    )
    grid.add_column(ratio=4, vertical="middle")
    grid.add_column(ratio=5)
    grid.add_row(left, right)

    console.print(Panel(
        grid,
        title=f"[bold yellow]🦆 GUS v{VERSION}[/bold yellow]",
        border_style="yellow",
        box=box.ROUNDED,
    ))


def get_bottom_toolbar(agent, routines) -> str:
    """Return a prompt_toolkit bottom toolbar string showing shortcuts and status."""
    from prompt_toolkit.formatted_text import HTML

    mode_badge = " [plan mode]" if agent.mode == "plan" else ""
    n_msgs     = len(agent.history)
    n_routines = len(routines.timed) + len(routines.every_turn)

    left  = " ? /help  ·  /plan  ·  /go  ·  /compact  ·  /loop  ·  /exit"
    right_parts = []
    if mode_badge:
        right_parts.append("📋 PLAN MODE")
    if n_routines:
        right_parts.append(f"{n_routines} routine{'s' if n_routines > 1 else ''}")
    right_parts.append(f"{n_msgs} msg{'s' if n_msgs != 1 else ''}")
    right = "  ·  ".join(right_parts) + " "

    # pad centre so right side is flush
    return HTML(
        f"<style fg='ansiyellow'>{left}</style>"
        f"<style fg='ansibrightblack'>{right}</style>"
    )


# ── Help ───────────────────────────────────────────────────────────────────

def print_help(skills: dict | None = None, agent_skills: dict | None = None) -> None:
    cmd_lines = ""
    if skills:
        rows = []
        for s in skills.values():
            tags = []
            if s.shell:
                tags.append("[dim]shell[/dim]")
            if s.max_iterations > 1:
                tags.append(f"[dim]loop×{s.max_iterations}[/dim]")
            if s.confirm:
                tags.append("[dim]confirm[/dim]")
            tag_str = "  " + " ".join(tags) if tags else ""
            rows.append(f"  [cyan]/{s.name:<14}[/cyan] — {s.description}{tag_str}")
        cmd_lines = "\n\n[bold]Custom Commands[/bold]\n" + "\n".join(rows)

    if agent_skills:
        rows = [f"  [cyan]/{s.name:<14}[/cyan] — {s.description}" for s in agent_skills.values()]
        cmd_lines += "\n\n[bold]Agent Skills[/bold]  [dim](agentskills.io)[/dim]\n" + "\n".join(rows)

    console.print(
        Panel(
            "[bold]Built-in[/bold]\n"
            "  [cyan]/help[/cyan]                    — this message\n"
            "  [cyan]/clear[/cyan]                   — clear conversation history\n"
            "  [cyan]/compact[/cyan]                 — summarise history → single context message\n"
            r"  [cyan]/cwd[/cyan] \[path]             — show or change working directory" + "\n"
            "  [cyan]/exit[/cyan]                   — quit\n\n"
            "[bold]Modes[/bold]\n"
            r"  [cyan]/plan[/cyan] \[task]            — plan mode: analyse only, no writes" + "\n"
            "  [cyan]/go[/cyan]                     — execute the current plan\n"
            "  [cyan]/agent[/cyan]                  — switch back to agent mode\n\n"
            "[bold]Loop & Routines[/bold]\n"
            r"  [cyan]/loop[/cyan] \[n] <prompt>      — repeat n times (default 3)" + "\n"
            r"  [cyan]/loop[/cyan] \[n] /<cmd>         — loop a command" + "\n"
            "  [cyan]/loop[/cyan] <time> <prompt>   — schedule: 30s 5m 1h 1d\n"
            "  [cyan]/loop[/cyan] every  <prompt>   — hook: before every prompt\n"
            "  [cyan]/loop[/cyan] list              — show active routines\n"
            "  [cyan]/loop[/cyan] stop <id>         — cancel a routine\n"
            "  [dim]e.g. /loop 1h summarise new commits[/dim]"
            f"{cmd_lines}\n\n"
            "[bold]Keyboard[/bold]\n"
            "  Ctrl+C                 — interrupt current run / exit",
            title="[bold yellow]🦆 GUS — Help[/bold yellow]",
            box=box.SIMPLE,
            border_style="yellow",
        )
    )
