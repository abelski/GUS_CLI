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
load_dotenv(CONFIG_DIR / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "google/gemma-4-31b-it:free")

_DEFAULT_FALLBACKS = ",".join([
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "moonshotai/kimi-k2.6:free",
])
# Tried in order when a model returns 429; override with AGENT_FREE_MODEL_FALLBACKS (comma-separated)
FREE_MODEL_FALLBACKS = [
    m.strip()
    for m in os.environ.get("AGENT_FREE_MODEL_FALLBACKS", _DEFAULT_FALLBACKS).split(",")
    if m.strip()
]

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
