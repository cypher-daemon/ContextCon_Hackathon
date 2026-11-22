"""exceptions.recovery (C7b). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def prepare_case(exc: ExceptionRecord, payment: Payment) -> RecoveryCase:
    """Prepare a recovery case."""
    raise NotImplementedError
