"""checks.runner (C2). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def run_checks(item: BankLine | Invoice | VendorMessage, resolution: Resolution, ctx: StageContext) -> list[CheckResult]:
    """Run checks in policy.checks_order."""
    raise NotImplementedError
