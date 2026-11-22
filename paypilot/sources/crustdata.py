"""sources.crustdata (B5). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def identify_by_domain(ctx: StageContext, domain: str, item_id: str) -> Any:
    """IdentifyResult. By domain only, never by name."""
    raise NotImplementedError
