"""io.batch (A3). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def load_items(request: Request, ctx: StageContext) -> list[BankLine | Invoice | VendorMessage | ParseFailure]:
    """Parse batch files; decimal string -> paise; fingerprint at load."""
    raise NotImplementedError
