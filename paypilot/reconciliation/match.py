"""reconciliation.match (C8). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def three_way(decision: Decision | None, payment: Payment | None, provider_view: ProviderPayment | None, statement: list[BankLine] | None, history: list[HistoryRecord] | None, policy: Any) -> ReconciliationResult:
    """Three-way match."""
    raise NotImplementedError
