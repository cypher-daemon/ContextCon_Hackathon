"""sources.webfetch (B6). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def fetch_identity_claim(ctx: StageContext, domain: str, item_id: str) -> Any | None:
    """IdentityClaim (company_website_claim) or None."""
    raise NotImplementedError
