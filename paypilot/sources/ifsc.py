"""sources.ifsc (B4). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def lookup(ctx: StageContext, ifsc: str, item_id: str) -> Any:
    """IfscResult: VALID | NOT_FOUND | UNAVAILABLE + Source."""
    raise NotImplementedError
