"""io.request (A3). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def load_request(path: Path) -> Request:
    """Load + validate request.json; sets source_path/source_sha."""
    raise NotImplementedError
