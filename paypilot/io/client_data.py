"""io.client_data (A3). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def load_client_data(client_id: str, cfg: RunConfig) -> tuple[ClientData, str]:
    """Load client files; fingerprint accounts at load; returns (data, data_hash)."""
    raise NotImplementedError
