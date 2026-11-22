"""Secrets from the environment / .env only (never logged). Owner: lane A."""
from __future__ import annotations

import os
from pathlib import Path

from paypilot.core.config import ConfigError

_PLACEHOLDERS = {"", "change-me-random-32-bytes"}


def load_dotenv(path: str | Path = ".env") -> None:
    """Tiny KEY=VALUE parser. Real environment variables win. No new dependency."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def bank_hmac_key(root: str | Path = ".") -> str:
    load_dotenv(Path(root) / ".env")
    key = os.environ.get("BANK_HMAC_KEY", "")
    if key in _PLACEHOLDERS:
        raise ConfigError("BANK_HMAC_KEY is missing or still the placeholder; set a random value in .env")
    return key
