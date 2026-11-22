"""payments.executor (A8). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def execute(payment: Payment, provider: PaymentProvider, ctx: StageContext) -> Payment:
    """Step-recorded payment execution."""
    raise NotImplementedError
