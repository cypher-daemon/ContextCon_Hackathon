"""Cost ledger (LedgerPort) over SQLite + the cost summary. Owner: lane A.

Rules (ARCHITECTURE §10): a row is reserved BEFORE every external call, in its own committed transaction (if that write fails
the call is never made: PersistenceFailure -> exit 70). After a crash RESERVED rows become UNCONFIRMED and still count.
An unknown price is UNKNOWN, never 0.
`call_key` includes the attempt: a repeat of the same request in a run is attempt+1, so earlier spend is never overwritten.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable

from paypilot.core import ids
from paypilot.core.db import tx
from paypilot.schemas import LedgerEntry, LedgerSummary

PIPELINE_STATES = ("NEW", "PARSED", "RESOLVED", "CHECKED", "DECIDED")


class LedgerError(Exception):
    """Illegal ledger transition (e.g. confirming a row that was never reserved)."""


class SqliteLedger:
    def __init__(self, con: sqlite3.Connection, clock: Callable[[], datetime]):
        self.con, self.clock = con, clock

    def _next_attempt(self, e: LedgerEntry) -> int:
        row = self.con.execute(
            "SELECT COALESCE(MAX(attempt),0) AS m FROM cost_ledger WHERE run_id=? AND COALESCE(item_id,'')=COALESCE(?,'') AND stage=? "
            "AND provider=? AND operation=? AND request_hash=?", (e.run_id, e.item_id, e.stage, e.provider, e.operation, e.request_hash)).fetchone()
        return max(e.attempt, row["m"] + 1)

    def _insert(self, e: LedgerEntry, status: str, units: float, estimated: float | None, actual: float | None) -> str:
        with tx(self.con):
            attempt = self._next_attempt(e)
            key = ids.call_key(e.run_id, e.item_id, e.stage, e.provider, e.operation, e.request_hash, attempt)
            self.con.execute(
                "INSERT INTO cost_ledger(call_key,run_id,session_id,item_id,stage,provider,operation,unit_type,units,estimated_inr,"
                "actual_inr,status,http_status,duration_ms,request_hash,attempt,ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, e.run_id, e.session_id, e.item_id, e.stage, e.provider, e.operation, e.unit_type, units, estimated, actual,
                 status, e.http_status if status == "CACHE_HIT" else None, None, e.request_hash, attempt, self.clock().isoformat()))
        return key

    # ---- LedgerPort ----
    def reserve(self, e: LedgerEntry) -> str:
        """Persist intent. Returns the call_key to confirm/fail. Raises PersistenceFailure if it cannot be written."""
        return self._insert(e, "RESERVED", 0.0, e.estimated_inr, None)

    def confirm(self, call_key: str, units: float, actual_inr: float | None, http_status: int | None, duration_ms: int) -> None:
        """`actual_inr=None` means the price is unknown: the row stays UNKNOWN in reports (never 0)."""
        self._update(call_key, "CONFIRMED", units=units, actual=actual_inr, http=http_status, ms=duration_ms)

    def fail(self, call_key: str, http_status: int | None, error: str) -> None:
        """A call that failed. With an HTTP status the provider answered (no charge assumed); with None (timeout, no response)
        the spend may have happened, so it still counts toward the budget and the unconfirmed total."""
        self._update(call_key, "FAILED", units=0.0, actual=None, http=http_status, ms=None)

    def cache_hit(self, e: LedgerEntry) -> None:
        self._insert(e, "CACHE_HIT", 0.0, 0.0, 0.0)

    def _update(self, call_key: str, status: str, *, units: float, actual: float | None, http: int | None, ms: int | None) -> None:
        with tx(self.con):
            cur = self.con.execute("UPDATE cost_ledger SET status=?, units=?, actual_inr=?, http_status=?, duration_ms=? "
                                   "WHERE call_key=? AND status='RESERVED'", (status, units, actual, http, ms, call_key))
            if cur.rowcount != 1:
                raise LedgerError(f"call {call_key} is not RESERVED; cannot move it to {status}")

    # ---- resume protocol ----
    def mark_unconfirmed(self, run_id: str) -> int:
        """Step 3 of the resume protocol: calls whose outcome we never recorded. They keep counting at their estimate."""
        with tx(self.con):
            return self.con.execute("UPDATE cost_ledger SET status='UNCONFIRMED' WHERE run_id=? AND status='RESERVED'", (run_id,)).rowcount


def summarize(con: sqlite3.Connection, run_id: str, *, limit_inr: float | None = None, unknown_reserve_inr: float = 1.0) -> LedgerSummary:
    """KNOWN = every confirmed call is priced. PARTIAL = some priced spend, some unknown (total is a lower bound).
    UNKNOWN = spend exists but nothing is priced (total None). Never reports 0 for a missing price."""
    rows = con.execute("SELECT * FROM cost_ledger WHERE run_id=?", (run_id,)).fetchall()
    known_total, spend_priced, unknown_n = 0.0, 0, 0
    by_provider: dict[str, float | None] = {}
    per_item: dict[str, float | None] = {}
    unconfirmed = 0.0
    for r in rows:
        if r["status"] in ("UNCONFIRMED",) or (r["status"] == "FAILED" and r["http_status"] is None):
            unconfirmed += r["estimated_inr"] if r["estimated_inr"] is not None else unknown_reserve_inr
        if r["status"] != "CONFIRMED":
            continue
        by_provider.setdefault(r["provider"], 0.0)
        if r["actual_inr"] is None:
            unknown_n += 1
            by_provider[r["provider"]] = None
            if r["item_id"]:
                per_item[r["item_id"]] = None
            continue
        known_total += r["actual_inr"]
        spend_priced += 1 if r["actual_inr"] > 0 else 0
        if by_provider[r["provider"]] is not None:
            by_provider[r["provider"]] += r["actual_inr"]
        if r["item_id"] and per_item.get(r["item_id"], 0.0) is not None:
            per_item[r["item_id"]] = per_item.get(r["item_id"], 0.0) + r["actual_inr"]
    status = "KNOWN" if unknown_n == 0 else ("PARTIAL" if spend_priced > 0 else "UNKNOWN")
    total = None if status == "UNKNOWN" else round(known_total, 6)

    n_items = con.execute("SELECT COUNT(*) FROM items WHERE run_id=?", (run_id,)).fetchone()[0]
    marks = ",".join("?" * len(PIPELINE_STATES))
    finished = con.execute(f"SELECT COUNT(*) FROM items WHERE run_id=? AND state NOT IN ({marks})", (run_id, *PIPELINE_STATES)).fetchone()[0]
    with_calls = {r["item_id"] for r in rows if r["item_id"] and r["status"] != "CACHE_HIT"}
    known_items = [v for v in per_item.values() if v is not None]
    return LedgerSummary(
        run_id=run_id, total_inr=total, cost_status=status, unconfirmed_inr=round(unconfirmed, 6),
        by_provider={k: (None if v is None else round(v, 6)) for k, v in sorted(by_provider.items())},
        calls=sum(1 for r in rows if r["status"] != "CACHE_HIT"), cache_hits=sum(1 for r in rows if r["status"] == "CACHE_HIT"),
        items=n_items, finished_items=finished,
        inr_per_item=(round(total / n_items, 6) if total is not None and n_items else None),
        worst_item_inr=(max(known_items) if known_items else (0.0 if status == "KNOWN" else None)),
        limit_inr=float(limit_inr or 0.0),
        zero_cost_items_pct=round(100.0 * (n_items - len(with_calls)) / n_items, 2) if n_items else 0.0)
