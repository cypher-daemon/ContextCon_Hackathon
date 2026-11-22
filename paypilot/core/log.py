"""JSONL logger that masks bank accounts and secrets. Owner: lane A. Never log a raw account number or a key."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DIGITS = re.compile(r"(?<![\w*])\d{9,18}(?![\w])")           # bank-account-like digit runs
_KEYS = re.compile(r"(rzp_(?:test|live)_\w+|Basic\s+[A-Za-z0-9+/=]{8,}|Bearer\s+\S+)")
_SECRET_FIELDS = {"authorization", "key_secret", "razorpayx_key_secret", "api_key", "crustdata_api_key", "bank_hmac_key", "secret"}


def mask_text(s: str) -> str:
    s = _DIGITS.sub(lambda m: "****" + m.group(0)[-4:], s)
    return _KEYS.sub("[REDACTED]", s)


def mask(obj: Any) -> Any:
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: "[REDACTED]" if str(k).lower() in _SECRET_FIELDS else mask(v) for k, v in obj.items()}
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
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)
