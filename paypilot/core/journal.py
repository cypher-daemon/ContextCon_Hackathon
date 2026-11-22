"""core.journal (A4). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def post_entry(conn: Any, entry: JournalEntry) -> None:
    """Write a balanced entry in the caller's transaction; asserts balance."""
    raise NotImplementedError
