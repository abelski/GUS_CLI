"""Tests for assistant-response streaming in ui.py.

These pin the append-only streaming behaviour introduced to fix the Rich `Live`
overflow bug, where a response taller than the terminal left every intermediate
frame behind in the scrollback (cascading-duplicate text).
"""
import ui


def test_streaming_is_append_only():
    """The streamed text must never go through a Rich Live region."""
    ui.print_assistant_start()
    assert ui._live_render is None          # no Live → no cursor-up re-render
    assert ui._live_text == ""

    ui.print_assistant_chunk("x")
    assert ui._live_render is None
    assert ui._live_text == "x"

    ui.print_assistant_chunk("y")
    assert ui._live_text == "xy"            # chunks accumulate

    ui.print_assistant_end()
    assert ui._live_render is None
    assert ui._live_text == ""              # state reset for the next turn


def test_each_chunk_printed_exactly_once():
    chunks = ["Hello ", "world ", "TOKEN_UNIQUE ", "tail"]
    with ui.console.capture() as cap:
        ui.print_assistant_start()
        for c in chunks:
            ui.print_assistant_chunk(c)
        ui.print_assistant_end()
    out = cap.get()
    assert out.count("TOKEN_UNIQUE") == 1
    for word in ("Hello", "world", "tail"):
        assert word in out


def test_long_response_not_duplicated():
    """Regression: a long, many-chunk answer must print each chunk once.

    The old Live renderer re-rendered the whole accumulated buffer on every
    chunk, so a marker would appear O(n^2) times in the captured output. Append-
    only streaming prints each chunk exactly once → exactly n occurrences.
    """
    n = 200
    line = "MARKER_LINE some answer content here. "   # marker has no spaces
    with ui.console.capture() as cap:
        ui.print_assistant_start()
        for _ in range(n):
            ui.print_assistant_chunk(line)
        ui.print_assistant_end()
    out = cap.get()
    assert out.count("MARKER_LINE") == n


def test_chunk_text_is_not_interpreted_as_markup():
    """Model output containing Rich markup/brackets must print literally."""
    with ui.console.capture() as cap:
        ui.print_assistant_start()
        ui.print_assistant_chunk("see [red]not-a-style[/red] and {braces}")
        ui.print_assistant_end()
    out = cap.get()
    assert "[red]not-a-style[/red]" in out
    assert "{braces}" in out
