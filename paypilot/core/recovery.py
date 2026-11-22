"""Resume protocol steps 3-4 (FAILURE_MODES §3): the reconcile-first gate. Owner: lane A.

One implementation, called by the CLI (run / resume) AND by the approval service itself, so no entry point can skip it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable

from paypilot.ledger.store import SqliteLedger


def begin_recovery(con: sqlite3.Connection, run_id: str, clock: Callable[[], datetime]) -> None:
    """RESERVED ledger rows -> UNCONFIRMED (they still count against the budget). Then refuse to do any new work while an attempt
    may have reached the provider without a recorded outcome: that must be reconciled first (payments.reconcile, task A8)."""
    SqliteLedger(con, clock).mark_unconfirmed(run_id)
    # A payment still CREATED (step NONE) has made no provider call, so there is nothing to reconcile. An attempt that was sent
    # (step beyond NONE) with no recorded outcome may have moved money.
    open_attempts = con.execute(
        "SELECT COUNT(*) FROM payment_attempts a JOIN payments p ON p.payment_id=a.payment_id JOIN items i ON i.item_id=p.item_id "
        "WHERE i.run_id=? AND a.step != 'NONE' AND a.outcome IN ('PENDING','UNKNOWN')", (run_id,)).fetchone()[0]
    if open_attempts:
        raise NotImplementedError("open payment attempts must be reconciled with the provider first (payments.reconcile, task A8)")
