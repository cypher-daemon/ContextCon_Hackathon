"""approvals.service (A7). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def approve(item_id: str, reviewer: str, reason: str | None, overrides: list[str]) -> Approval:
    """Bind decision_hash, enqueue, execute inline."""
    raise NotImplementedError
