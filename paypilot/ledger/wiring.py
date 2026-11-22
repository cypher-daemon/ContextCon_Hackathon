"""The one place the cost ports are assembled for a run (used by the CLI and by tests). Owner: lane A."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable

from paypilot.ledger.budget import Budget, request_limits
from paypilot.ledger.cache import SqliteCache
from paypilot.ledger.store import SqliteLedger
from paypilot.schemas import RunConfig


def make_ports(con: sqlite3.Connection, *, run_id: str, session_id: str, cfg: RunConfig,
               clock: Callable[[], datetime]) -> tuple[SqliteLedger, SqliteCache, Budget]:
    """Ledger, cache and budget for one run. The budget is also the ledger's pre-reserve guard, so the per-item cost limit
    is enforced whether or not the caller passed an item_id to `budget.reserve` (lane B's ExtClient does not)."""
    budget = Budget(con, run_id, cfg_limits=cfg.limits, request_limits=request_limits(con, run_id),
                    unknown_reserve_inr=float(cfg.costs.get("unknown_reserve_inr", 1.0)))
    ledger = SqliteLedger(con, clock, run_id=run_id, session_id=session_id, costs=cfg.costs,
                          pre_reserve=lambda est, item: budget.reserve(est, item_id=item))
    return ledger, SqliteCache(con, clock), budget
