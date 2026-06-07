"""Tests for the URL guard — the anti-fabrication backstop for final answers."""
import url_guard as g
from agent import Agent


# ── url_guard primitives ─────────────────────────────────────────────────────

def test_normalize_scheme_host_lowercased_path_kept():
    assert g.normalize("HTTPS://YouTube.com/Watch?v=ABC/") == "https://youtube.com/Watch?v=ABC"


def test_extract_strips_wrapping_punctuation():
    text = "see https://a.com/x, and (https://b.com)."
    assert g.extract_urls(text) == {"https://a.com/x", "https://b.com"}


def test_web_search_hrefs_are_verified():
    result = "1. Title\n   https://real.com/page\n   snippet"
    assert "https://real.com/page" in g.verified_urls_from_tool("web_search", "", result)


def test_successful_web_fetch_verifies_arg_url():
    v = g.verified_urls_from_tool("web_fetch", '{"url":"https://docs.site/x"}', "page text")
    assert "https://docs.site/x" in v


def test_failed_web_fetch_verifies_nothing():
    # A 404 means the link does NOT exist — it must not count as verified.
    v = g.verified_urls_from_tool(
        "web_fetch", '{"url":"https://fake.com/nope"}',
        "Error: HTTP 404 Not Found — https://fake.com/nope")
    assert v == set()


def test_find_unverified_flags_only_unknown_urls():
    answer = "Watch https://real.com/page and https://made-up.com/xyz now."
    assert g.find_unverified(answer, {"https://real.com/page"}) == ["https://made-up.com/xyz"]


# ── run_turn integration ─────────────────────────────────────────────────────

def _agent_with_responses(responses):
    a = Agent(client=None, model="x", cwd=".")
    seq = iter(responses)
    a._stream_response = lambda: next(seq)
    return a


def test_fabricated_url_triggers_one_correction_pass():
    a = _agent_with_responses([
        ("Top video: https://youtube.com/watch?v=FAKE123", []),
        ("I couldn't verify that link, so I removed it.", []),
    ])
    a.run_turn("most viewed kite videos?")
    assert a._url_correction_done is True
    assert any(m["role"] == "user" and "Automated link check" in (m.get("content") or "")
               for m in a.history)
    assert a._last_response.startswith("I couldn't verify")


def test_persistent_fabrication_warns_once(monkeypatch):
    import ui
    seen = {}
    monkeypatch.setattr(ui, "print_url_guard_warning", lambda urls: seen.update(urls=urls))
    a = _agent_with_responses([
        ("Link: https://fake.com/a", []),
        ("Still here: https://fake.com/a", []),  # ignores the correction
    ])
    a.run_turn("give me a link")
    assert seen.get("urls") == ["https://fake.com/a"]


def test_user_supplied_url_is_not_flagged():
    a = _agent_with_responses([("That link https://github.com/x/y is what you sent.", [])])
    a.run_turn("is https://github.com/x/y valid?")
    assert a._url_correction_done is False
