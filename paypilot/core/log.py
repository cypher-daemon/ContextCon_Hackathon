"""JSONL logger that masks bank accounts and secrets. Owner: lane A. Never log a raw account number or a key."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bank-account-like digit runs, with optional single space/dash separators. 32+ char hex strings (HMAC fingerprints) are kept.
_DIGITS = re.compile(r"\b[0-9a-fA-F]{32,}\b|(?<!\d)\d(?:[ -]?\d){8,19}(?!\d)")
_KEYS = re.compile(r"(rzp_(?:test|live)_\w+|Basic\s+[A-Za-z0-9+/=]{8,}|Bearer\s+\S+)")
_SECRET_WORDS = ("secret", "token", "password", "authorization", "api_key", "apikey", "hmac")  # substring match on key names


def _mask_digits(m: re.Match) -> str:
    t = m.group(0)
    if re.fullmatch(r"[0-9a-fA-F]{32,}", t):
        return t
    return "****" + re.sub(r"\D", "", t)[-4:]


def mask_text(s: str) -> str:
    s = _DIGITS.sub(_mask_digits, s)
    return _KEYS.sub("[REDACTED]", s)


def mask(obj: Any) -> Any:
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        masked = mask_text(str(obj))
        return masked if masked != str(obj) else obj      # a long numeric (e.g. an int account number) is masked
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: "[REDACTED]" if any(w in str(k).lower() for w in _SECRET_WORDS) else mask(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask(v) for v in obj]
    return obj


class JsonlLogger:
    def __init__(self, directory: str | Path, name: str = "paypilot.jsonl", mask_accounts: bool = True):
        self.path = Path(directory) / name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mask_accounts = mask_accounts

    def log(self, event: str, **fields: Any) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        line = json.dumps(mask(rec) if self.mask_accounts else rec, default=str, sort_keys=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
