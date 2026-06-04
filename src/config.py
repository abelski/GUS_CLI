import logging
import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

VERSION = "0.1.0"

# Walk up from src/ to find the project root .env
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = os.environ.get("AGENT_MODEL", "google/gemma-4-31b-it:free")

# Tried in order when a model returns 429
FREE_MODEL_FALLBACKS = [
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-coder:free",
    "moonshotai/kimi-k2.6:free",
]

MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "2048"))
WORKING_DIR = os.environ.get("AGENT_WORKING_DIR", str(_root))


LOG_FILE = str(_root / "gus.log")


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
