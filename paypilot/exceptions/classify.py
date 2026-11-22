"""exceptions.classify (C7a). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def classify(item: Any, decision: Decision | None, recon: ReconciliationResult | None, payment: Payment | None, policy: Any) -> list[ExceptionDraft]:
    """Pure; lane A upserts."""
    raise NotImplementedError
