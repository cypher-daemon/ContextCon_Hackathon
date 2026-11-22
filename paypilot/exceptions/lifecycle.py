"""exceptions.lifecycle (C7b). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def can_transition(frm: ExceptionStatus, to: ExceptionStatus, evidence_refs: list[str]) -> bool:
    """RESOLVED/RECOVERED need evidence."""
    raise NotImplementedError
