"""resolve.normalize (B1). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def descriptor_key(s: str, policy: Any) -> str:
    """Normalised bank descriptor."""
    raise NotImplementedError


def name_key(s: str, policy: Any) -> str:
    """Normalised vendor name."""
    raise NotImplementedError


def invoice_canonical(s: str, policy: Any) -> str:
    """Canonical invoice number."""
    raise NotImplementedError
