"""core.pipeline (A4). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def drive(run_id: str, ctx: StageContext, provider: PaymentProvider) -> RunSummary:
    """ingest -> resolve -> check -> decide -> classify -> post/queue/hold."""
    raise NotImplementedError
