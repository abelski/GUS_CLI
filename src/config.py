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
