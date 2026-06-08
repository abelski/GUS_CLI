"""Drive a real headless browser (Playwright/Chromium) for JavaScript-heavy and
single-page-app sites that the static ``web_fetch`` tool cannot handle.

``web_fetch`` is a plain HTTP GET — it never runs JavaScript, so on a React/Vue/
Angular SPA it only sees an empty shell. This tool launches a real Chromium via
Playwright, renders the page, and can click buttons, fill forms, and capture
downloads triggered by interaction.

Design notes:
- **Optional dependency.** Playwright is imported lazily inside the worker
  thread; if it isn't installed the tool returns install instructions instead of
  crashing, and GUS still starts/runs without it.
- **Single dedicated thread.** Playwright's sync API is bound to the thread it
  was created on, but GUS may dispatch tool calls from different worker threads.
  So every browser operation is funnelled through one long-lived worker thread
  via a job queue — calls from any thread are serialised and run where the
  browser actually lives.
- **Sandboxed.** Downloads and screenshots are written inside the working dir.
"""
import os
import queue
import threading
from pathlib import Path

from ._sandbox import resolve, sandbox_check

_INSTALL_HINT = (
    "Error: the optional 'playwright' package is required for the browser tool.\n"
    "Install it once:\n"
    "  python3 -m pip install playwright\n"
    "  python3 -m playwright install chromium\n"
    "Then retry. (GUS runs fine without it; only the browser tool needs it.)"
)
_CHROMIUM_HINT = (
    "Error: Playwright is installed but the Chromium browser binary is missing.\n"
    "Install it once with:  python3 -m playwright install chromium"
)

_SNAPSHOT_TEXT_CHARS = 2500
_MAX_ELEMENTS = 30
_DOWNLOAD_SUBDIR = "downloads"


def _default_headless() -> bool:
    """Default window mode, read live from AGENT_BROWSER_HEADLESS (default: on)."""
    return os.environ.get("AGENT_BROWSER_HEADLESS", "1").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


def _resolve_headless(mode: str) -> bool:
    """Map the per-call ``mode`` ('headed'/'headless'/'') to a headless bool."""
    m = (mode or "").strip().lower()
    if m in ("headed", "head", "visible", "gui"):
        return False
    if m in ("headless", "background"):
        return True
    return _default_headless()


class _BrowserWorker:
    """Owns the Playwright session on a single thread; runs jobs from a queue."""

    def __init__(self) -> None:
        self._jobs: "queue.Queue" = queue.Queue()
        self._thread: "threading.Thread | None" = None
        self._lock = threading.Lock()
        # Session state — only ever touched on the worker thread.
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.headless = True  # mode of the currently-open browser, if any

    # ── worker-thread plumbing ──────────────────────────────────────────────
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gus-browser")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, reply = self._jobs.get()
            if fn is None:  # not used today, but lets the thread be retired cleanly
                reply.put((True, None))
                return
            try:
                reply.put((True, fn(self)))
            except Exception as e:  # noqa: BLE001 — relayed to the caller verbatim
                reply.put((False, e))

    def submit(self, fn, timeout: float = 90.0):
        """Run ``fn(worker)`` on the browser thread and return its result."""
        with self._lock:
            self._ensure_thread()
            reply: "queue.Queue" = queue.Queue()
            self._jobs.put((fn, reply))
        try:
            ok, val = reply.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"browser operation timed out after {timeout:.0f}s")
        if ok:
            return val
        raise val

    # ── session lifecycle (called on the worker thread) ─────────────────────
    def ensure_browser(self, headless: bool = True) -> None:
        if self.page is not None:
            if self.headless == headless:
                return
            # A different window mode was requested — relaunch in that mode.
            self.teardown()
        from playwright.sync_api import sync_playwright  # lazy: ImportError if absent
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=headless)
        self.context = self.browser.new_context(accept_downloads=True)
        self.page = self.context.new_page()
        self.headless = headless

    def teardown(self) -> None:
        for closer in (
            lambda: self.context and self.context.close(),
            lambda: self.browser and self.browser.close(),
            lambda: self.pw and self.pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self.pw = self.browser = self.context = self.page = None


_worker = _BrowserWorker()


# ── helpers (run on the worker thread) ──────────────────────────────────────

def _locate(page, selector: str):
    """Resolve ``selector`` as a Playwright selector, else fall back to a visible
    text / button-name match. Returns a Locator pointing at the first match."""
    # 1) Treat as a native Playwright selector (css=, text=, role=, xpath=, raw css).
    try:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    # 2) Visible text (case-insensitive, substring).
    try:
        loc = page.get_by_text(selector, exact=False)
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    # 3) A button/link by accessible name.
    for role in ("button", "link"):
        try:
            loc = page.get_by_role(role, name=selector)
            if loc.count() > 0:
                return loc.first
        except Exception:
            pass
    raise RuntimeError(f"no element matched selector or text {selector!r}")


def _snapshot(page) -> str:
    """A compact view of the current page: title, url, visible text, and the
    interactive elements the model can target next."""
    try:
        title = page.title()
    except Exception:
        title = ""
    url = page.url
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    body = " ".join(body.split())
    if len(body) > _SNAPSHOT_TEXT_CHARS:
        body = body[:_SNAPSHOT_TEXT_CHARS] + " …(truncated)"

    elements: list[str] = []
    media: list[str] = []
    try:
        handles = page.query_selector_all(
            "button, a[href], input, textarea, select, [role=button], "
            "audio, video, source, a[download]"
        )
    except Exception:
        handles = []
    for el in handles:
        if len(elements) >= _MAX_ELEMENTS:
            break
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            # Media sources carry the actual file URL — surface it for downloads.
            if tag in ("audio", "video", "source"):
                src = el.get_attribute("src") or ""
                if src:
                    media.append(f"<{tag}> {src[:160]}")
                continue
            if not el.is_visible():
                continue
            # Skip low-signal inputs (checkbox/radio/hidden) that would crowd out
            # the actionable buttons and links; they're still reachable via CSS.
            if tag == "input" and (el.get_attribute("type") or "text").lower() in (
                "checkbox", "radio", "hidden"
            ):
                continue
            label = (el.inner_text() or "").strip()
            if not label:
                label = (el.get_attribute("placeholder") or el.get_attribute("aria-label")
                         or el.get_attribute("name") or el.get_attribute("value") or "").strip()
            label = " ".join(label.split())[:60]
            href = el.get_attribute("href") if tag == "a" else None
            if href and href.startswith(("http", "/")) and not href.startswith("/#"):
                elements.append(f"<{tag}> {label!r} → {href[:160]}")
            elif label:
                elements.append(f"<{tag}> {label!r}")
        except Exception:
            continue

    parts = [f"Title: {title}", f"URL: {url}", "", "Visible text:", body]
    if elements:
        parts += ["", "Interactive elements (click/fill by their text):"]
        parts += [f"  - {e}" for e in elements]
    if media:
        parts += ["", "Media/file sources (download via the browser tool or web_fetch):"]
        parts += [f"  - {m}" for m in media]
    return "\n".join(parts)


def _download_dir(cwd: str) -> str:
    target = resolve(_DOWNLOAD_SUBDIR, cwd)
    err = sandbox_check(target, cwd)
    if err:
        raise RuntimeError(err)
    Path(target).mkdir(parents=True, exist_ok=True)
    return target


# ── action implementations ──────────────────────────────────────────────────

def _do_navigate(cwd, url, mode="", **_):
    if not url:
        return "Error: action 'navigate' requires a url."
    headless = _resolve_headless(mode)
    def job(w):
        w.ensure_browser(headless)
        w.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:  # let SPA XHR settle; best-effort
            w.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        return _snapshot(w.page)
    window = "headless" if headless else "headed (visible window)"
    return f"Navigated to {url}  [{window}]\n\n" + _worker.submit(job, timeout=60)


def _do_read(cwd, selector="", **_):
    def job(w):
        if w.page is None:
            return "Error: no page open. Call action='navigate' first."
        if selector:
            loc = _locate(w.page, selector)
            return loc.inner_text()
        return _snapshot(w.page)
    return _worker.submit(job, timeout=30)


def _do_click(cwd, selector="", **_):
    if not selector:
        return "Error: action 'click' requires a selector (CSS or visible text)."
    def job(w):
        if w.page is None:
            return "Error: no page open. Call action='navigate' first."
        _locate(w.page, selector).click(timeout=15000)
        try:
            w.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        return _snapshot(w.page)
    return f"Clicked {selector!r}\n\n" + _worker.submit(job, timeout=45)


def _do_fill(cwd, selector="", text="", submit=False, **_):
    if not selector:
        return "Error: action 'fill' requires a selector (CSS or visible text)."
    def job(w):
        if w.page is None:
            return "Error: no page open. Call action='navigate' first."
        loc = _locate(w.page, selector)
        loc.fill(text)
        if submit:
            loc.press("Enter")
            try:
                w.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        return _snapshot(w.page)
    return f"Filled {selector!r}\n\n" + _worker.submit(job, timeout=45)


def _do_download(cwd, url="", selector="", path="", mode="", **_):
    if not selector:
        return "Error: action 'download' requires a selector for the element that triggers the download."
    headless = _resolve_headless(mode)
    out_dir = _download_dir(cwd)
    # Resolve an explicit output path up front (sandbox-checked) if given.
    explicit = None
    if path:
        explicit = resolve(path, cwd)
        err = sandbox_check(explicit, cwd)
        if err:
            return err

    def job(w):
        w.ensure_browser(headless)
        if url:
            w.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                w.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        loc = _locate(w.page, selector)
        try:
            with w.page.expect_download(timeout=60000) as info:
                loc.click(timeout=15000)
            download = info.value
        except Exception:
            # The click didn't produce a download — it likely navigated instead.
            # Surface where we landed so the agent can adapt.
            return ("NO_DOWNLOAD", _snapshot(w.page))
        name = download.suggested_filename or "download.bin"
        target = explicit or str(Path(out_dir) / name)
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        download.save_as(target)
        size = Path(target).stat().st_size
        return ("OK", target, size)

    result = _worker.submit(job, timeout=90)
    if isinstance(result, tuple) and result and result[0] == "OK":
        _, target, size = result
        return f"Downloaded {size:,} bytes to {target}"
    if isinstance(result, tuple) and result and result[0] == "NO_DOWNLOAD":
        return ("No download was triggered by clicking "
                f"{selector!r} — the click probably navigated instead. "
                "Resulting page:\n\n" + result[1])
    return str(result)


def _do_screenshot(cwd, path="", **_):
    target = resolve(path or "screenshot.png", cwd)
    err = sandbox_check(target, cwd)
    if err:
        return err
    def job(w):
        if w.page is None:
            return "Error: no page open. Call action='navigate' first."
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        w.page.screenshot(path=target, full_page=True)
        return f"Saved screenshot to {target}"
    return _worker.submit(job, timeout=45)


def _do_close(cwd, **_):
    _worker.submit(lambda w: w.teardown(), timeout=30)
    return "Browser closed."


_ACTIONS = {
    "navigate": _do_navigate,
    "read": _do_read,
    "click": _do_click,
    "fill": _do_fill,
    "download": _do_download,
    "screenshot": _do_screenshot,
    "close": _do_close,
}


SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser",
        "description": (
            "Drive a real headless Chromium browser (via Playwright) for JavaScript-heavy or "
            "single-page-app (React/Vue/Angular) sites that the static web_fetch tool cannot "
            "handle — it executes JS, renders the page, and can click buttons, fill forms, and "
            "download files triggered by interaction. The browser session persists across calls "
            "until action='close'. Prefer web_fetch for simple static pages; use this when a page "
            "needs JS to render or requires clicking/typing. Typical flow: navigate → read/click/"
            "fill → download → close. Requires the optional 'playwright' package; if it's missing "
            "the tool returns one-time install instructions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "read", "click", "fill", "download", "screenshot", "close"],
                    "description": (
                        "navigate: open a URL. read: return rendered text (whole page, or one "
                        "element via selector). click: click an element. fill: type into an input. "
                        "download: click an element that triggers a file download and save it. "
                        "screenshot: capture the page. close: end the session."
                    ),
                },
                "url": {"type": "string", "description": "URL for 'navigate', or optional starting URL for 'download'."},
                "selector": {"type": "string", "description": "Target element — a CSS selector OR its visible text (used by click, fill, download, and optionally read)."},
                "text": {"type": "string", "description": "Text to type, for action='fill'."},
                "submit": {"type": "boolean", "description": "For 'fill': press Enter after typing. Default false."},
                "path": {"type": "string", "description": "Output file path inside the working directory, for 'download' or 'screenshot'. Optional."},
                "mode": {
                    "type": "string",
                    "enum": ["headless", "headed"],
                    "description": (
                        "Window mode, applied when the browser launches (on 'navigate' or "
                        "'download'): 'headless' (default, no window) or 'headed' (open a visible "
                        "browser window — use when the user wants to watch, or for sites needing "
                        "manual login/captcha). Changing mode mid-session relaunches the browser. "
                        "Defaults to the AGENT_BROWSER_HEADLESS setting."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}


def run(cwd: str, action: str = "", url: str = "", selector: str = "",
        text: str = "", submit: bool = False, path: str = "", mode: str = "") -> str:
    handler = _ACTIONS.get(action)
    if handler is None:
        return (f"Error: unknown browser action {action!r}. "
                f"Valid actions: {', '.join(_ACTIONS)}.")
    try:
        return handler(cwd, url=url, selector=selector, text=text,
                       submit=submit, path=path, mode=mode)
    except ImportError:
        return _INSTALL_HINT
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            return _CHROMIUM_HINT
        return f"Error: browser {action} failed: {msg}"
