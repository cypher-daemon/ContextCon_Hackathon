"""decide.engine (C6). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def decide(item: BankLine | Invoice | VendorMessage, resolution: Resolution, checks: list[CheckResult], policy: Any) -> Decision:
    """All reasons, precedence, decision_hash."""
    raise NotImplementedError
