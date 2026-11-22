"""sources.http (B3). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def make_ext_client(cfg: RunConfig, ledger: LedgerPort, cache: CachePort, budget: BudgetPort) -> ExtClient:
    """ExtClient over the ports: rate limit, retry, redaction."""
    raise NotImplementedError
