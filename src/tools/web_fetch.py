"""Fetch a URL and return its text content, stripping HTML."""
import re
import ssl
import urllib.request
import urllib.error
from html.parser import HTMLParser


def _ssl_context() -> ssl.SSLContext:
    """Return best available SSL context: certifi > system > unverified."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

_USER_AGENT = "GUS/1.0 (AI agent)"
_MAX_CHARS  = 8_000


class _TextExtractor(HTMLParser):
    """Strip HTML tags; skip script/style blocks."""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "noscript", "head"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._text.append(data)

    def get_text(self) -> str:
        raw = " ".join(self._text)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": (
            "Fetch the content of a URL and return it as plain text. "
            "Use to read documentation, blog posts, GitHub READMEs, API references, "
            "or any specific page found via web_search. "
            "HTML is stripped to readable text; JSON and plain text are returned as-is."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to fetch (http or https).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Max characters to return (default {_MAX_CHARS}, max 30000).",
                },
            },
            "required": ["url"],
        },
    },
}


def run(url: str, cwd: str, max_chars: int = _MAX_CHARS) -> str:
    max_chars = min(max_chars, 30_000)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(512_000)
    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} {e.reason} — {url}"
    except urllib.error.URLError as e:
        return f"Error: could not reach {url}: {e.reason}"
    except Exception as e:
        return f"Error: {e}"

    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].split(";")[0].strip()
    try:
        text = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")

    if "html" in content_type.lower() or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        extractor = _TextExtractor()
        extractor.feed(text)
        text = extractor.get_text()

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[truncated — {len(text) - max_chars} more chars not shown]"

    return text or "(empty response)"
