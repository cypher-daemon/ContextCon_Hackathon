"""Cost and time limits (BudgetPort). Owner: lane A.

Cost is derived from the ledger itself (reserved + unconfirmed + confirmed, so restarts and crashes never under-report spend).
A null price reserves `unknown_reserve_inr` against the limit; the REPORTED cost stays UNKNOWN. A free call (estimate 0) is never
blocked by the cost limit. The stricter of the request's and the policy's limit applies. The time limit is per session, so
`resume` after a time stop gets a fresh window instead of stopping immediately.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Callable

from paypilot.context import BudgetExceeded, TimeExceeded
from paypilot.schemas import Limits

_EPS = 1e-9
_SPEND = ("CASE WHEN actual_inr IS NOT NULL THEN actual_inr WHEN estimated_inr IS NOT NULL THEN estimated_inr ELSE ? END")


def stricter(*vals: float | int | None) -> float | None:
    present = [float(v) for v in vals if v is not None]
    return min(present) if present else None


class Budget:
    def __init__(self, con: sqlite3.Connection, run_id: str, *, cfg_limits: dict, request_limits: Limits | None = None,
                 unknown_reserve_inr: float = 1.0, monotonic: Callable[[], float] = time.monotonic):
        self.con, self.run_id, self.unknown = con, run_id, float(unknown_reserve_inr)
        rl = request_limits or Limits()
        self.max_cost = stricter(cfg_limits.get("max_cost_inr"), rl.max_cost_inr)
        self.max_per_item = stricter(cfg_limits.get("max_cost_inr_per_item"))
        self.max_seconds = stricter(cfg_limits.get("max_runtime_seconds"), rl.max_runtime_seconds)
        self._mono, self._t0 = monotonic, monotonic()

    def spent(self, item_id: str | None = None) -> float:
        """Committed + reserved spend for the run (or one item)."""
        sql = (f"SELECT COALESCE(SUM({_SPEND}),0) FROM cost_ledger WHERE run_id=? AND (status IN ('RESERVED','UNCONFIRMED','CONFIRMED') "
               "OR (status='FAILED' AND http_status IS NULL))")
        args: list = [self.unknown, self.run_id]
        if item_id is not None:
            sql, args = sql + " AND item_id=?", args + [item_id]
        return float(self.con.execute(sql, args).fetchone()[0])

    def reserve(self, estimated_inr: float | None, *, item_id: str | None = None) -> None:
        """Raises BudgetExceeded if this call would pass a limit. Call it BEFORE the ledger row and the external call."""
        amount = self.unknown if estimated_inr is None else float(estimated_inr)
        if amount <= 0:
            return
        if self.max_cost is not None and self.spent() + amount > self.max_cost + _EPS:
            raise BudgetExceeded(f"run cost limit ₹{self.max_cost:g} would be exceeded (spent ₹{self.spent():g} + ₹{amount:g})")
        if item_id is not None and self.max_per_item is not None and self.spent(item_id) + amount > self.max_per_item + _EPS:
            raise BudgetExceeded(f"per-item cost limit ₹{self.max_per_item:g} would be exceeded for {item_id}")

    def check_time(self) -> None:
        if self.max_seconds is not None and self._mono() - self._t0 >= self.max_seconds:
            raise TimeExceeded(f"runtime limit {self.max_seconds:g}s reached")


def request_limits(con: sqlite3.Connection, run_id: str) -> Limits:
    """The request's own limits, stored with the run (snapshot `_request.limits`); they combine with config by 'stricter wins'."""
    import json
    row = con.execute("SELECT policy_snapshot_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    lim = (json.loads(row["policy_snapshot_json"]).get("_request") or {}).get("limits") if row else None
    return Limits(**lim) if lim else Limits()


def effective_cost_limit(con: sqlite3.Connection, run_id: str, cfg_limits: dict) -> float | None:
    return stricter(cfg_limits.get("max_cost_inr"), request_limits(con, run_id).max_cost_inr)
