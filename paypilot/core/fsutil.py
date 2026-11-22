"""Atomic file writes (tmp + fsync + rename): a reader never sees a half-written output. Owner: lane A."""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    return p
