"""Configuration, loaded once at import.

The service reads its credentials from the environment. In development those
live in `.env`, and nothing was loading that file — `preflight.py` parsed it
itself and the demo script passed variables inline, so the gap only appeared
when Razorpay delivered a real webhook to a real running service and it was
refused for want of a secret that was sitting on disk.

Real environment variables always win, so this is safe in deployment where
there is no `.env` at all.
"""

from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).parent.parent.parent
ENV_FILE = ROOT / ".env"


def load_env(path: pathlib.Path | None = None) -> int:
    """Populate the environment from a dotenv file. Returns keys loaded.

    Existing environment variables are never overwritten.
    """
    f = path or ENV_FILE
    if not f.exists():
        return 0
    loaded = 0
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


load_env()


def webhook_secret() -> str:
    return os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")


def database_path() -> str:
    return os.environ.get("UNHALTED_DB", "unhalted.db")


def model_name() -> str:
    return os.environ.get("UNHALTED_MODEL", "")


def model_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "")


def model_base_url() -> str:
    return os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
