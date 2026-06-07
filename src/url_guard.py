"""URL verification guard — catches fabricated links in final answers.

The hallucination failure mode is GUS presenting URLs (and the facts attached
to them) that it never actually retrieved. A system-prompt rule asks the model
not to do this, but a weak free model may ignore it. This module is the code-
level backstop: it tracks every URL that genuinely came from a tool result
(``web_search`` hits, a successful ``web_fetch``) or from the user's own
message, then flags any URL in the final answer that isn't in that "verified"
set so the agent can re-check or drop it before the user trusts it.
"""
import json
import re

# http(s) URLs; stop at whitespace and characters that usually wrap a link.
_URL_RE = re.compile(r"""https?://[^\s<>"'`)\]}]+""", re.IGNORECASE)

# Trailing characters that are punctuation around a URL, not part of it.
_TRAILING = ".,;:!?)]}>'\"`·…"


def normalize(url: str) -> str:
    """Canonical form for comparison: drop the fragment and a trailing slash,
    lowercase the scheme and host, keep path/query case-sensitive."""
    u = url.strip().rstrip(_TRAILING)
    u = u.split("#", 1)[0]
    m = re.match(r"(https?://)([^/]+)(.*)", u, re.IGNORECASE)
    if m:
        u = m.group(1).lower() + m.group(2).lower() + m.group(3)
    if u.endswith("/"):
        u = u[:-1]
    return u


def extract_urls(text: str) -> set[str]:
    """Every http(s) URL in `text`, normalized for comparison."""
    if not text:
        return set()
    return {normalize(m) for m in _URL_RE.findall(text) if normalize(m)}


def _is_error_result(result: str) -> bool:
    return bool(result) and result.strip().lower().startswith("error")


def verified_urls_from_tool(name: str, arguments: str, result: str) -> set[str]:
    """URLs a single tool call genuinely surfaced — safe to treat as real.

    A failed ``web_fetch`` (result starts with "Error:") verifies nothing, so
    the URL it tried is intentionally excluded; that is exactly the case where
    a hallucinated link gets caught.
    """
    if _is_error_result(result):
        return set()
    urls = extract_urls(result)
    if name == "web_fetch":
        try:
            u = (json.loads(arguments or "{}") or {}).get("url")
        except (ValueError, TypeError):
            u = None
        if u:
            urls.add(normalize(u))
    return urls


def find_unverified(answer: str, verified: set[str]) -> list[str]:
    """URLs present in `answer` but absent from the `verified` set."""
    return sorted(u for u in extract_urls(answer) if u not in verified)
