"""reports.writer (A5). STUB from the A0 contract commit: replace NotImplementedError, keep the signature."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from paypilot.schemas import *  # noqa: F401,F403
from paypilot.context import *  # noqa: F401,F403

def write_outputs(run_id: str, ctx: StageContext) -> list[Path]:
    """Atomic writes of results.jsonl, report.md, summary.json, ..."""
    raise NotImplementedError
