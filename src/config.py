import json
import logging
import os
import sys
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

VERSION = "0.1.0"


def _get_config_dir() -> Path:
    # When packaged by PyInstaller, store config in ~/.gus/ so the user can
    # edit .env and logs without digging into the frozen bundle's temp dir.
    if getattr(sys, "frozen", False):
        d = Path.home() / ".gus"
        d.mkdir(exist_ok=True)
        return d
    return Path(__file__).resolve().parent.parent


CONFIG_DIR = _get_config_dir()
ENV_FILE = CONFIG_DIR / ".env"
load_dotenv(ENV_FILE)


def save_env_var(name: str, value: str) -> None:
    """Write/replace ``name=value`` in .env and update the live process env.

    Single canonical .env writer shared by the key setup and the model cache.
    """
    lines: list[str] = []
    if ENV_FILE.is_file():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            lines[i] = f"{name}={value}\n"
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{name}={value}\n")
    ENV_FILE.write_text("".join(lines), encoding="utf-8")
    os.environ[name] = value

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Always-available catch-all: OpenRouter routes this to a working free model.
# Used as the startup default and as the last-resort 429 fallback.
FALLBACK_MODEL = "openrouter/free"

DEFAULT_MODEL = os.environ.get("AGENT_MODEL", FALLBACK_MODEL)

_DEFAULT_FALLBACKS = ",".join([
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "moonshotai/kimi-k2.6:free",
    FALLBACK_MODEL,
])
# Tried in order when a model returns 429; override with AGENT_FREE_MODEL_FALLBACKS (comma-separated)
FREE_MODEL_FALLBACKS = [
    m.strip()
    for m in os.environ.get("AGENT_FREE_MODEL_FALLBACKS", _DEFAULT_FALLBACKS).split(",")
    if m.strip()
]

# Offline fallback for the /settings model picker when the live OpenRouter
# list can't be fetched (no network). Each entry is (model_id, label, note).
PREDEFINED_MODELS: list[tuple[str, str, str]] = [
    (FALLBACK_MODEL,                          "OpenRouter Free (auto)", "free · auto-routes to a free model"),
    ("google/gemma-4-31b-it:free",            "Gemma 4 31B",        "free · fast, capable default"),
    ("openai/gpt-oss-120b:free",              "GPT-OSS 120B",       "free · large open model"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B",     "free · solid all-rounder"),
    ("qwen/qwen3-coder:free",                 "Qwen3 Coder",        "free · tuned for code"),
    ("moonshotai/kimi-k2.6:free",             "Kimi K2.6",          "free · long context"),
]

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# .env key holding the cached free-model list (JSON: [[id, label, note], …]).
FREE_MODELS_ENV = "AGENT_FREE_MODELS"


def _is_zero_price(v) -> bool:
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return False


def load_cached_free_models() -> "list[tuple[str, str, str]] | None":
    """Return the free-model list cached in .env (AGENT_FREE_MODELS), or None
    if it is missing/unparseable."""
    raw = os.environ.get(FREE_MODELS_ENV, "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        models = [tuple(x) for x in data if isinstance(x, list) and len(x) == 3]
        return models or None
    except (ValueError, TypeError):
        return None


def save_free_models(models: list[tuple[str, str, str]]) -> None:
    """Persist the free-model list to .env (AGENT_FREE_MODELS) as one-line JSON."""
    save_env_var(FREE_MODELS_ENV, json.dumps([list(m) for m in models], ensure_ascii=False))


def fetch_free_models(timeout: float = 12.0) -> list[tuple[str, str, str]]:
    """Fetch the live list of free models from OpenRouter.

    Mirrors the website filter https://openrouter.ai/models?q=free: keeps
    models whose id ends with ``:free`` or whose prompt+completion price is 0,
    excluding image/audio generation models. Returns ``(id, label, note)``
    tuples sorted by name with ``openrouter/free`` pinned on top. Returns the
    static PREDEFINED_MODELS list (same object) on any network/parse error.
    """
    import httpx  # bundled with the openai dependency (uses certifi for TLS)
    try:
        resp = httpx.get(
            OPENROUTER_MODELS_URL,
            timeout=timeout,
            headers={"User-Agent": f"GUS/{VERSION}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception:
        return PREDEFINED_MODELS

    models: list[tuple[str, str, str]] = []
    for m in data:
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        is_free = mid.endswith(":free") or (
            _is_zero_price(pricing.get("prompt"))
            and _is_zero_price(pricing.get("completion"))
        )
        if not is_free:
            continue
        # Skip generation models (image/audio output) — not usable as a chat
        # agent. Keep text-only-output models; keep when modality is unknown.
        out_mods = (m.get("architecture") or {}).get("output_modalities")
        if out_mods and set(out_mods) - {"text"}:
            continue
        label = (m.get("name") or mid).removesuffix(" (free)")
        ctx = m.get("context_length")
        note = f"free · {int(ctx):,} ctx" if ctx else "free"
        models.append((mid, label, note))

    if not models:
        return PREDEFINED_MODELS

    # Pin the always-on catch-all to the top, then sort the rest by name.
    if not any(mid == FALLBACK_MODEL for mid, _, _ in models):
        models.append((FALLBACK_MODEL, "OpenRouter Free (auto)",
                       "free · auto-routes to a free model"))
    models.sort(key=lambda x: (x[0] != FALLBACK_MODEL, x[1].lower()))
    return models


def get_free_models(refresh: bool = False) -> list[tuple[str, str, str]]:
    """Free-model list for the picker, reading the .env cache first.

    Without ``refresh`` the cached list (AGENT_FREE_MODELS) is returned when
    present. Otherwise the live OpenRouter list is fetched; on a successful
    fetch it is written back to .env so the next run loads instantly/offline.
    """
    if not refresh:
        cached = load_cached_free_models()
        if cached:
            return cached
    live = fetch_free_models()
    if live is not PREDEFINED_MODELS:  # network fetch succeeded
        save_free_models(live)
    return live


MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "2048"))
WORKING_DIR = os.environ.get("AGENT_WORKING_DIR", str(Path.cwd()))

# Safety / reliability limits
# Max tool-use iterations inside a single agent turn before we stop and report.
MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "50"))
# Max times the autonomous goal loop will re-prompt before giving up.
MAX_GOAL_ITERATIONS = int(os.environ.get("AGENT_MAX_GOAL_ITERATIONS", "25"))
# Auto-compact history once the estimated context size crosses this many tokens.
# 0 disables automatic compaction.
COMPACT_THRESHOLD_TOKENS = int(os.environ.get("AGENT_COMPACT_THRESHOLD", "100000"))
# Transient-error retry policy for model calls.
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "3"))
RETRY_BASE_DELAY = float(os.environ.get("AGENT_RETRY_BASE_DELAY", "1.0"))
# Cap on concurrent worker threads when a turn emits many tool calls.
MAX_TOOL_WORKERS = int(os.environ.get("AGENT_MAX_TOOL_WORKERS", "8"))
# MCP JSON-RPC response timeout (seconds).
MCP_TIMEOUT = float(os.environ.get("AGENT_MCP_TIMEOUT", "60"))

LOG_FILE = os.environ.get("AGENT_LOG_FILE", str(CONFIG_DIR / "gus.log"))


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("gus")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


def get_client() -> OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is not set. "
            "Export it or add it to a .env file."
        )
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
    )
