"""Terminal UI — GUS agent personality and rendering."""
import contextlib
import getpass
import json
import random
import threading
import time
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich import box

# ── Celeste palette ─────────────────────────────────────────────────────────
# PICO-8-derived colours from Celeste. BRAND (lavender) replaces the old
# yellow chrome as the primary brand colour; SKY/PINK are the secondary
# accents. The duck mascot keeps its warm yellow body (DUCK) by design.
BRAND = "#b39ddb"   # lavender   — titles, borders, GUS label, spinner (was yellow)
SKY   = "#5fcde4"   # sky blue   — You label, plan mode, water, asides (was cyan)
PINK  = "#ff77a8"   # hot pink   — music, sub-agents, skills (was magenta)
OK    = "#00e436"   # green      — success / celebrate
ERR   = "#ff004d"   # red        — errors / confusion
DUCK  = "yellow"    # mascot body — intentionally unchanged

# highlight=False: let Markdown handle its own colouring; prevents Rich's
# auto-highlighter from recolouring table cell content mid-render.
console = Console(highlight=False)

# Serialises all Rich Live/Status output so parallel sub-agents don't corrupt the display.
console_lock = threading.Lock()
_subagent_depth_lock = threading.Lock()


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
        spinner_style=SKY,
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
    F     = f"bold {DUCK}"                  # solid fill blocks: ▄ ▀ █
    FB    = f"on {DUCK}"                    # yellow bg for interior spaces
    EY    = f"bold black on {DUCK}"         # normal eyes
    BL    = f"bold dark_orange on {DUCK}"   # bill / beak detail
    RED   = f"bold {ERR} on {DUCK}"         # staring / confused accents
    GRN   = f"bold {OK} on {DUCK}"          # celebrating
    TILDE = f"bold {SKY}"                   # water / wind waves (outside body)

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
            row(("~~(", TILDE), ("█", F), ("  ", FB), ("flap flap", f"bold black on {DUCK}"), ("  ", FB), ("█", F), (")~~", TILDE)),
            row(("~~~", TILDE), ("█", F), ("    ", FB), ("~~~~", f"bold {SKY} on {DUCK}"), ("     ", FB), ("█", F), ("~~~", TILDE)),
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
            row(LE, ("  ", FB), ("z z z", f"dim white on {DUCK}"), ("      ", FB), RE),
            row(BOT),
        ],

        # ── dancing frames (cycle dance1→dance4) ──────────────────────────
        "dance1": [
            row(TOP),
            row(LE, ("  ", FB), ("◉", EY), ("     ", FB), ("◉", EY), ("    ", FB), RE),
            row(LE, ("  ", FB), ("▬♪▬▬▬", BL), ("      ", FB), RE),
            row(LE, ("  ", FB), ("♫   ♩  ♪   ", f"bold {PINK} on {DUCK}"), RE),
            row(BOT),
        ],

        "dance2": [
            row(("\\", f"bold {PINK}"), TOP),
            row(LE, ("  ", FB), ("★", f"bold {PINK} on {DUCK}"), ("     ", FB), ("★", f"bold {PINK} on {DUCK}"), ("    ", FB), RE),
            row(LE, ("  ", FB), ("▬▬♫▬▬", BL), ("      ", FB), RE),
            row(LE, ("  ", FB), ("♩  ♪  ♫    ", f"bold {PINK} on {DUCK}"), RE),
            row(BOT),
        ],

        "dance3": [
            row(TOP),
            row(LE, ("  ", FB), ("◉", EY), ("     ", FB), ("◉", EY), ("    ", FB), RE),
            row(LE, ("   ", FB), ("▬▬▬▬♪", BL), ("     ", FB), RE),
            row(LE, (" ", FB), ("♪   ♩  ♫    ", f"bold {PINK} on {DUCK}"), RE),
            row(BOT),
        ],

        "dance4": [
            row(("/", f"bold {PINK}"), TOP),
            row(LE, ("  ", FB), ("^", f"bold {PINK} on {DUCK}"), ("     ", FB), ("^", f"bold {PINK} on {DUCK}"), ("    ", FB), RE),
            row(LE, ("  ", FB), ("~~~♬~~~", f"bold {PINK} on {DUCK}"), ("    ", FB), RE),
            row(LE, ("  ", FB), ("♪ ♫  ♩ ♪   ", f"bold {PINK} on {DUCK}"), RE),
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


# ── Dancing loading screen ────────────────────────────────────────────────

_DANCE_FRAMES = ["dance1", "dance2", "dance3", "dance4"]

# ── Hello splash ──────────────────────────────────────────────────────────

_HELLO_PHRASES = [
    "you had me at hello.",                            # Jerry Maguire
    "to infinity and beyond!",                         # Toy Story
    "just keep swimming.",                             # Finding Nemo
    "adventure is out there!",                         # Up
    "I am Groot.",                                     # Guardians of the Galaxy
    "why so serious?",                                 # The Dark Knight
    "I'll be back.",                                   # Terminator
    "may the Force be with you.",                      # Star Wars
    "you is kind, you is smart, you is important.",    # The Help
    "hakuna matata.",                                  # The Lion King
    "I volunteer as tribute!",                         # The Hunger Games
    "nobody puts Baby in a corner.",                   # Dirty Dancing
    "life is like a box of chocolates.",               # Forrest Gump
    "do or do not. there is no try.",                  # Star Wars
    "you complete me.",                                # Jerry Maguire
    "I'm the king of the world!",                      # Titanic
    "I'm kind of a big deal.",                         # Anchorman
    "my precious.",                                    # The Lord of the Rings
    "with great power comes great responsibility.",    # Spider-Man
    "you can't handle the truth!",                     # A Few Good Men
]


def _speech_bubble(phrase: str) -> Text:
    """Comic-style speech bubble, tail pointing down toward the duck."""
    inner_w = max(len(phrase), 26)
    lpad    = (inner_w - len(phrase)) // 2
    rpad    = inner_w - len(phrase) - lpad
    tail_x  = inner_w // 2 + 1   # column of the ┬ / │ tail

    top  = "  ╭" + "─" * (inner_w + 2) + "╮"
    mid  = "  │ " + " " * lpad + phrase + " " * rpad + " │"
    bot  = "  ╰" + "─" * tail_x + "┬" + "─" * (inner_w + 1 - tail_x) + "╯"
    tail = " " * (tail_x + 3) + "│"

    t = Text()
    t.append(top  + "\n", style="bold white")
    t.append(mid  + "\n", style=f"bold {BRAND}")
    t.append(bot  + "\n", style="bold white")
    t.append(tail + "\n", style="bold white")
    return t


def print_hello_splash() -> None:
    """Animated hello: dancing duck + comic speech bubble, then flies away."""
    phrase = random.choice(_HELLO_PHRASES)
    bubble = _speech_bubble(phrase)

    # ── dancing phase ─────────────────────────────────────────────────────
    with Live(console=console, refresh_per_second=5, transient=True) as live:
        for i in range(12):
            duck = _duck(_DANCE_FRAMES[i % len(_DANCE_FRAMES)])
            body = Text()
            body.append_text(bubble)
            body.append_text(duck)
            body.append(f"\n\n  \"{phrase}\"", style=f"bold {BRAND} italic")
            live.update(Panel(
                body,
                border_style=BRAND,
                box=box.ROUNDED,
                title=f"[bold {BRAND}]🦆 GUS says hi![/bold {BRAND}]",
            ))
            time.sleep(0.2)

    # ── flying-away phase (transient → vanishes cleanly) ──────────────────
    with Live(console=console, refresh_per_second=4, transient=True) as live:
        body = Text(justify="center")
        body.append_text(_duck("flying"))
        body.append("\n\n  wheee!  🌟", style=f"bold {SKY}")
        live.update(Panel(
            body,
            border_style=SKY,
            box=box.ROUNDED,
            title=f"[bold {SKY}]🦆 here we go![/bold {SKY}]",
        ))
        time.sleep(0.6)

@contextlib.contextmanager
def loading_dance(label: str = "Loading…"):
    """Animate a dancing duck while a blocking operation runs, then vanish."""
    stop = threading.Event()

    def _run() -> None:
        i = 0
        with Live(console=console, refresh_per_second=5, transient=True) as live:
            while not stop.is_set():
                duck  = _duck(_DANCE_FRAMES[i % len(_DANCE_FRAMES)])
                dots  = "●" * ((i % 3) + 1) + "○" * (3 - (i % 3))
                body  = Text(justify="center")
                body.append_text(duck)
                body.append(f"\n\n  {label}  {dots}", style=f"bold {BRAND}")
                live.update(Panel(body, border_style=BRAND, box=box.ROUNDED,
                                  title=f"[bold {BRAND}]🦆 GUS[/bold {BRAND}]"))
                i += 1
                time.sleep(0.25)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=2)


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
    console.print(f"\n[bold {SKY}]You[/bold {SKY}]  {message}")


def print_assistant_start() -> None:
    global _live_render, _live_text
    console.print(f"\n[bold {BRAND}]GUS[/bold {BRAND}]")
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


def handle_resize() -> None:
    """Repair Rich Live output after a terminal/window resize.

    A resize reflows already-printed wrapped lines, so the line count Rich
    cached for the live region (`LiveRender._shape`) no longer matches the
    screen. The next frame would then move the cursor up by the wrong amount
    and erase the wrong rows — the "corruption" seen when the window is
    resized mid-render. Dropping the cached shape makes Rich redraw the region
    cleanly from the current cursor instead of overwriting stale rows.

    Best-effort and exception-safe: it runs from a signal handler, so it must
    never raise. Covers the streaming-response Live and the thinking spinner.
    """
    with console_lock:
        targets = []
        if _live_render is not None:
            targets.append(_live_render)
        if _active_status is not None:
            inner = getattr(_active_status, "_live", None)
            if inner is not None:
                targets.append(inner)
        for live in targets:
            try:
                lr = getattr(live, "_live_render", None)
                if lr is not None:
                    lr._shape = None
                live.refresh()
            except Exception:
                pass


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
    console.print(f"\n[bold {BRAND}]  {emoji} {tool_name}[/bold {BRAND}]  [dim italic]{action}[/dim italic]",
                  highlight=False)
    if len(args_str) < 300:
        console.print(f"[dim]{args_str}[/dim]")


def print_tool_result(tool_name: str, result: str, error: bool = False) -> None:
    color        = ERR if error else "dim"
    symbol       = "✗" if error else "✓"
    symbol_color = ERR if error else OK
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
    console.print(f"\n[bold {BRAND}]{random.choice(_DONE)}[/bold {BRAND}]")


def print_empty_response() -> None:
    """Model returned no text and no tool call — nothing happened this turn.

    Usually means every free model was rate-limited or returned an empty
    completion. Shown instead of the celebratory "done" so an empty turn is
    never mistaken for success."""
    console.print(
        f"\n[bold {BRAND}]*gus blinks — empty response*[/bold {BRAND}]  "
        "[dim]the model returned nothing (often rate-limited free models). "
        "Try again, or /settings to switch model.[/dim]"
    )


def print_celebrate() -> None:
    """Big celebration — full celebrating duck."""
    duck = _duck("celebrating")
    console.print(Panel(duck, border_style=OK, box=box.HEAVY,
                        title=f"[bold {OK}]🎉 GUS! 🎉[/bold {OK}]"))


def print_flying_away() -> None:
    """Shown on Ctrl+C interrupt."""
    duck = _duck("flying")
    console.print(Panel(duck, border_style=SKY, box=box.ROUNDED,
                        title=f"[bold {SKY}]🦆 bye bye ...[/bold {SKY}]"))


def print_confused(message: str) -> None:
    """Shown on error — confused duck + message."""
    phrase = random.choice(_ERRORS)
    duck   = _duck("confused")
    body   = Text()
    body.append_text(duck)
    body.append(f"\n\n{phrase}\n")
    body.append(message, style=f"bold {ERR}")
    console.print(Panel(body, border_style=ERR, box=box.ROUNDED,
                        title=f"[bold {ERR}]gus?[/bold {ERR}]"))


def print_diving() -> None:
    """Shown briefly when web search starts."""
    duck = _duck("diving")
    console.print(Panel(duck, border_style=SKY, box=box.SIMPLE,
                        title=f"[bold {SKY}]🌊 searching...[/bold {SKY}]"))


# ── Btw / side question ────────────────────────────────────────────────────

def print_btw_result(question: str, answer: str) -> None:
    console.print(Panel(
        f"[dim italic]{question}[/dim italic]\n\n{answer}",
        title=f"[bold {SKY}]💬 aside[/bold {SKY}]",
        border_style=SKY,
        box=box.SIMPLE,
    ))


# ── Usage / cost ────────────────────────────────────────────────────────────

def print_usage(agent) -> None:
    msgs      = len(agent.history)
    in_tok    = agent.total_input_tokens
    out_tok   = agent.total_output_tokens
    total_tok = in_tok + out_tok
    name_line = f"  Session: [bold]{agent.session_name}[/bold]\n" if agent.session_name else ""
    goal_line = f"  Goal: [italic]{agent.goal}[/italic]\n" if agent.goal else ""
    tok_line  = (
        f"  Tokens in:  {in_tok:,}\n"
        f"  Tokens out: {out_tok:,}\n"
        f"  Total:      {total_tok:,}"
        if total_tok else "  Token counts not available (model did not report usage)."
    )
    console.print(Panel(
        f"{name_line}{goal_line}"
        f"  Messages in history: {msgs}\n"
        f"  Model: {agent.model}\n\n"
        f"{tok_line}",
        title=f"[bold {BRAND}]📊 GUS — Usage[/bold {BRAND}]",
        border_style=BRAND,
        box=box.SIMPLE,
    ))


# ── Context window breakdown ────────────────────────────────────────────────

def print_context(agent) -> None:
    """Show current context usage by category + session API totals."""
    stats = agent.context_stats()
    total = stats["total"]

    def pct(n: int) -> str:
        return f"{n / total * 100:.0f}%" if total else "—"

    def bar(n: int, width: int = 26) -> str:
        filled = round(n / total * width) if total else 0
        return f"[{BRAND}]" + "█" * filled + f"[/{BRAND}][dim]" + "░" * (width - filled) + "[/dim]"

    tbl = Table(show_header=True, box=box.SIMPLE, padding=(0, 1),
                show_edge=False, header_style=f"bold {BRAND}")
    tbl.add_column("Category",      style=SKY,    min_width=18)
    tbl.add_column("Tokens",        style="white", justify="right", min_width=8)
    tbl.add_column("%",             style="dim",   justify="right", min_width=5)
    tbl.add_column("",              min_width=28)

    rows = [
        ("System prompt",    stats["system"]),
        ("User messages",    stats["user"]),
        ("Assistant output", stats["assistant"]),
        ("Tool results",     stats["tool"]),
    ]
    for label, n in rows:
        tbl.add_row(label, f"{n:,}", pct(n), bar(n))

    tbl.add_section()
    tbl.add_row("[bold]Total in context[/bold]", f"[bold]{total:,}[/bold]", "", "")

    api_in  = agent.total_input_tokens
    api_out = agent.total_output_tokens
    cache_r = agent.total_cache_read_tokens
    turns   = agent.total_turns
    msgs    = len(agent.history)

    api_tbl = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    api_tbl.add_column(style=SKY,    min_width=22)
    api_tbl.add_column(style="white", justify="right")

    api_tbl.add_row("API input tokens",     f"{api_in:,}"  if api_in  else "—")
    api_tbl.add_row("API output tokens",    f"{api_out:,}" if api_out else "—")
    if cache_r:
        api_tbl.add_row("Cache reads",      f"{cache_r:,}")
    api_tbl.add_row("Turns completed",      str(turns))
    api_tbl.add_row("Messages in history",  str(msgs))
    api_tbl.add_row("Model",                agent.model)

    note = Text()
    note.append("Context estimate  ", style="bold")
    note.append("(chars ÷ 4 — approximate)", style="dim")

    console.print(Panel(
        note,
        title=f"[bold {BRAND}]📐 GUS — Context[/bold {BRAND}]",
        border_style=BRAND,
        box=box.SIMPLE,
        subtitle="[dim]/compact to shrink[/dim]",
    ))
    console.print(tbl)
    console.print(f"\n[bold {BRAND}]Session API totals[/bold {BRAND}]")
    console.print(api_tbl)


# ── Skills list ─────────────────────────────────────────────────────────────

def print_skills_list(skills: dict | None = None, agent_skills: dict | None = None) -> None:
    lines = []
    if skills:
        lines.append("[bold]Custom Commands[/bold]")
        for s in skills.values():
            tags = []
            if s.shell:          tags.append("[dim]shell[/dim]")
            if s.max_iterations > 1: tags.append(f"[dim]loop×{s.max_iterations}[/dim]")
            if s.confirm:        tags.append("[dim]confirm[/dim]")
            tag_str = "  " + " ".join(tags) if tags else ""
            lines.append(f"  [{SKY}]/{s.name:<16}[/{SKY}] {s.description}{tag_str}")
    if agent_skills:
        lines.append("\n[bold]Agent Skills[/bold]  [dim](agentskills.io)[/dim]")
        for s in agent_skills.values():
            lines.append(f"  [{SKY}]/{s.name:<16}[/{SKY}] {s.description}")
    if not lines:
        lines.append("  No skills loaded.")
    console.print(Panel(
        "\n".join(lines),
        title=f"[bold {BRAND}]🦆 GUS — Skills[/bold {BRAND}]",
        border_style=BRAND,
        box=box.SIMPLE,
    ))


# ── Settings / model picker ──────────────────────────────────────────────────

def print_model_picker(models, current: str) -> None:
    """Render the predefined model list as a numbered, selectable menu.

    `models` is a list of (model_id, label, note) tuples; `current` is the
    active model id (marked with ●).
    """
    tbl = Table(show_header=True, box=box.SIMPLE, padding=(0, 1),
                show_edge=False, header_style=f"bold {BRAND}")
    tbl.add_column("#",     style=f"bold {SKY}", justify="right", min_width=2)
    tbl.add_column("",      min_width=1)
    tbl.add_column("Model", style="white",     min_width=18)
    tbl.add_column("ID",    style="dim",        min_width=28)
    tbl.add_column("Notes", style="dim")

    for i, (mid, label, note) in enumerate(models, start=1):
        active = f"[bold {OK}]●[/bold {OK}]" if mid == current else " "
        tbl.add_row(str(i), active, label, mid, note)

    console.print(Panel(
        tbl,
        title=f"[bold {BRAND}]⚙️  GUS — Settings · Model[/bold {BRAND}]",
        border_style=BRAND,
        box=box.ROUNDED,
        subtitle="[dim]enter a number to switch · Enter to keep current · q to cancel[/dim]",
    ))
    console.print(f"  [dim]Current model:[/dim] [{SKY}]{current}[/{SKY}]")


def print_settings_saved(model: str) -> None:
    console.print(Panel(
        f"[bold {OK}]✓[/bold {OK}] Model set to [{SKY}]{model}[/{SKY}]\n"
        "[dim]Saved to .env (AGENT_MODEL) — persists across restarts.[/dim]",
        title=f"[bold {OK}]⚙️  Settings saved[/bold {OK}]",
        border_style=OK,
        box=box.SIMPLE,
    ))


# ── Goal achieved ───────────────────────────────────────────────────────────

def print_goal_achieved(goal: str) -> None:
    console.print(Panel(
        f"[bold {OK}]✓[/bold {OK}] {goal}",
        title=f"[bold {OK}]🎯 Goal achieved![/bold {OK}]",
        border_style=OK,
        box=box.ROUNDED,
    ))


# ── Compact ────────────────────────────────────────────────────────────────

def print_compact_result(old_count: int, summary: str) -> None:
    console.print(Panel(
        f"[dim]{summary}[/dim]",
        title=f"[bold {SKY}]🗜 Compacted {old_count} messages → 1 summary[/bold {SKY}]",
        border_style=SKY,
        box=box.SIMPLE,
    ))


# ── Sub-agent ──────────────────────────────────────────────────────────────

_subagent_depth = 0

def print_subagent_start(task: str) -> None:
    global _subagent_depth
    with _subagent_depth_lock:
        _subagent_depth += 1
        depth = _subagent_depth
    console.rule(f"[bold {PINK}]🤖 Sub-agent #{depth}: {task[:72]}[/bold {PINK}]",
                 style=PINK)


def print_subagent_end(failed: bool = False) -> None:
    global _subagent_depth
    label  = f"[bold {ERR}]✗ Sub-agent failed[/bold {ERR}]" if failed else f"[bold {PINK}]✓ Sub-agent done[/bold {PINK}]"
    console.rule(label, style=f"{PINK} dim")
    with _subagent_depth_lock:
        _subagent_depth = max(0, _subagent_depth - 1)


# ── Mode indicator ─────────────────────────────────────────────────────────

def print_mode_change(mode: str) -> None:
    if mode == "plan":
        console.print(f"[bold {SKY}]📋 Plan mode — GUS will analyse and plan, not execute.[/bold {SKY}]")
        console.print("[dim]  Use /go when ready to execute the plan.[/dim]")
    else:
        console.print(f"[bold {BRAND}]⚡ Agent mode — GUS will execute directly.[/bold {BRAND}]")


# ── Notifications ──────────────────────────────────────────────────────────

def print_error(message: str) -> None:
    console.print(f"\n[bold {ERR}]Error:[/bold {ERR}] {message}")


def print_warning(message: str) -> None:
    console.print(f"[bold {BRAND}]Warning:[/bold {BRAND}] {message}")


def print_info(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def print_url_guard_checking(count: int) -> None:
    plural = "s" if count != 1 else ""
    console.print(
        f"\n[dim]🔎 Verifying {count} link{plural} that no tool retrieved this turn…[/dim]"
    )


def print_url_guard_warning(urls: list[str]) -> None:
    """Warn that links in the answer could not be verified as real."""
    body = "\n".join(f"  • {u}" for u in urls)
    console.print(Panel(
        f"[bold {ERR}]These links were not retrieved from any source and may not "
        f"exist:[/bold {ERR}]\n{body}\n"
        "[dim]Treat them as unverified — GUS could not confirm they resolve.[/dim]",
        title=f"[bold {ERR}]⚠  Unverified links[/bold {ERR}]",
        border_style=ERR,
        box=box.ROUNDED,
    ))


def print_skill_load(skill_name: str) -> None:
    console.print(f"\n[bold {PINK}]  🧠 GUS load skill[/bold {PINK}] [bold]{skill_name}[/bold]")


# ── Banner ─────────────────────────────────────────────────────────────────

def print_banner(model: str, working_dir: str, ctx=None, mode: str = "agent") -> None:
    from config import VERSION

    raw_user = getpass.getuser()
    username = raw_user.replace("_", " ").replace("-", " ").title()

    # ── top info table ─────────────────────────────────────────────────────
    info = Table(show_header=False, box=None, padding=(0, 1), show_edge=False)
    info.add_column(style=f"bold {SKY}",    min_width=12)
    info.add_column(style="white")
    info.add_row("CLI Version", VERSION)
    info.add_row("Model",       model)
    info.add_row("CWD",         working_dir)
    if mode == "plan":
        info.add_row("Mode", f"[bold {SKY}]PLAN[/bold {SKY}]")
    if ctx and ctx.skills:
        info.add_row("Commands", "  ".join(f"/{s}" for s in ctx.skills))
    console.print(info)
    console.print()

    # ── left column: welcome + duck ────────────────────────────────────────
    # Model/CWD live in the info table above, so they're omitted here to keep
    # the banner short enough to fit an 80×24 terminal without scrolling.
    left = Text(justify="center")
    left.append(f"Welcome back {username}!\n\n", style=f"bold {BRAND}")
    left.append_text(_duck("normal"))
    if ctx and ctx.instructions:
        left.append("\n\nagents.md loaded", style="dim")

    # ── right column: a few essential tips ────────────────────────────────
    # Kept deliberately short — this is the tallest column and therefore sets
    # the panel height. See /help for the full command list.
    right = Text(no_wrap=False, overflow="fold")
    right.append("Tips for getting started\n", style=f"bold {BRAND}")
    right.append("  /plan <task>   plan first\n",       style="dim")
    right.append("  /go            run the plan\n",      style="dim")
    right.append("  /goal <cond>   loop until done\n",   style="dim")
    right.append("  /loop 1h …     schedule a routine\n", style="dim")
    right.append("  /settings      pick a model\n",      style="dim")
    right.append("  /help          all commands\n",      style="dim")
    right.append("\n  more: /btw /recap /skills /export", style="dim")

    # ── two-column grid with divider ───────────────────────────────────────
    grid = Table(
        box=box.MINIMAL,
        show_header=False,
        expand=True,
        show_edge=False,
        padding=(0, 2),
        border_style=f"{BRAND} dim",
    )
    grid.add_column(ratio=4, vertical="middle")
    grid.add_column(ratio=5)
    grid.add_row(left, right)

    console.print(Panel(
        grid,
        title=f"[bold {BRAND}]🦆 GUS v{VERSION}[/bold {BRAND}]",
        border_style=BRAND,
        box=box.ROUNDED,
    ))


def get_bottom_toolbar(agent, routines) -> str:
    """Return a prompt_toolkit bottom toolbar string showing shortcuts and status."""
    from prompt_toolkit.formatted_text import HTML

    n_msgs     = len(agent.history)
    n_routines = len(routines.timed) + len(routines.every_turn)

    left  = " ? /help  ·  /plan  ·  /go  ·  /compact  ·  /loop  ·  /btw  ·  /goal  ·  /exit"
    right_parts = []
    if agent.session_name:
        right_parts.append(agent.session_name)
    if agent.mode == "plan":
        right_parts.append("📋 PLAN")
    if agent.goal:
        short_goal = agent.goal[:30] + ("…" if len(agent.goal) > 30 else "")
        right_parts.append(f"🎯 {short_goal}")
    if n_routines:
        right_parts.append(f"{n_routines} routine{'s' if n_routines > 1 else ''}")
    right_parts.append(f"{n_msgs} msg{'s' if n_msgs != 1 else ''}")
    right = "  ·  ".join(right_parts) + " "

    return HTML(
        f"<style fg='{BRAND}'>{left}</style>"
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
            rows.append(f"  [{SKY}]/{s.name:<14}[/{SKY}] — {s.description}{tag_str}")
        cmd_lines = "\n\n[bold]Custom Commands[/bold]\n" + "\n".join(rows)

    if agent_skills:
        rows = [f"  [{SKY}]/{s.name:<14}[/{SKY}] — {s.description}" for s in agent_skills.values()]
        cmd_lines += "\n\n[bold]Agent Skills[/bold]  [dim](agentskills.io)[/dim]\n" + "\n".join(rows)

    console.print(
        Panel(
            rf"""[bold]Conversation[/bold]
  [{SKY}]/help[/{SKY}]                    — this message
  [{SKY}]/clear[/{SKY}]  /new  /reset     — clear conversation history
  [{SKY}]/compact[/{SKY}]                 — summarise history → single context message
  [{SKY}]/btw[/{SKY}] <question>          — ask a side question (no history change)
  [{SKY}]/recap[/{SKY}]                   — one-sentence session summary
  [{SKY}]/rename[/{SKY}] [name]           — name this session
  [{SKY}]/export[/{SKY}] [file.md]        — export conversation (clipboard or file)
  [{SKY}]/copy[/{SKY}]                    — copy last response to clipboard
  [{SKY}]/cwd[/{SKY}] \[path]             — show or change working directory
  [{SKY}]/exit[/{SKY}]  /quit             — quit

[bold]Model & Usage[/bold]
  [{SKY}]/settings[/{SKY}]                — pick model (cached free list; 'refresh' to re-fetch)
  [{SKY}]/model[/{SKY}] [id]              — pick from list, or switch directly
  [{SKY}]/usage[/{SKY}]  /cost            — token usage and session stats
  [{SKY}]/context[/{SKY}]                 — context window breakdown by category

[bold]Modes[/bold]
  [{SKY}]/plan[/{SKY}] \[task]            — plan mode: analyse only, no writes
  [{SKY}]/go[/{SKY}]                     — execute the current plan
  [{SKY}]/agent[/{SKY}]                  — switch back to agent mode

[bold]Goal — autonomous loop until done[/bold]
  [{SKY}]/goal[/{SKY}] <condition>        — set a goal; GUS keeps working until met
  [{SKY}]/goal[/{SKY}]                    — show active goal
  [{SKY}]/goal[/{SKY}] clear              — cancel goal
  [dim]e.g. /goal all tests pass[/dim]

[bold]Skills[/bold]
  [{SKY}]/skills[/{SKY}]                  — list all loaded skills
  [{SKY}]/reload-skills[/{SKY}]           — rescan .gus/commands/ and .gus/skills/ from disk

[bold]Loop & Routines[/bold]
  [{SKY}]/loop[/{SKY}] \[n] <prompt>      — repeat n times (default 3)
  [{SKY}]/loop[/{SKY}] \[n] /<cmd>         — loop a command
  [{SKY}]/loop[/{SKY}] <time> <prompt>   — schedule: 30s 5m 1h 1d
  [{SKY}]/loop[/{SKY}] every  <prompt>   — hook: before every prompt
  [{SKY}]/loop[/{SKY}] list              — show active routines
  [{SKY}]/loop[/{SKY}] stop <id>         — cancel a routine
  [dim]e.g. /loop 1h summarise new commits[/dim]{cmd_lines}

[bold]Keyboard[/bold]
  Ctrl+C                 — stop the current run / background routines
  Ctrl+D                 — quit GUS (or /exit)""",
            title=f"[bold {BRAND}]🦆 GUS — Help[/bold {BRAND}]",
            box=box.SIMPLE,
            border_style=BRAND,
        )
    )
