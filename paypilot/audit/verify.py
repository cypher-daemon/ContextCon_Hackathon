"""audit.verify (A17). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def verify_run(run_id: str, cfg: RunConfig) -> AuditVerifyReport:
    """Replay decisions from stored facts + policy snapshot."""
    raise NotImplementedError
