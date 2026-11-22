"""resolve.matcher (B2). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def resolve_vendor(item: BankLine | Invoice | VendorMessage, ctx: StageContext) -> Resolution:
    """gstin/cin -> fingerprint -> exact -> alias -> fuzzy; tie; lookalike."""
    raise NotImplementedError
