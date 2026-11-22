"""Resume protocol steps 3-4 (FAILURE_MODES §3): the reconcile-first gate. Owner: lane A.

One implementation, called by the CLI (run / resume) AND by the approval service itself, so no entry point can skip it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable

from paypilot.ledger.store import SqliteLedger
from paypilot.schemas import RunConfig


def begin_recovery(con: sqlite3.Connection, run_id: str, clock: Callable[[], datetime], cfg: RunConfig | None = None,
                   session_id: str = "recovery") -> None:
    """RESERVED ledger rows -> UNCONFIRMED (they still count against the budget). Then reconcile every payment of the run with its
    provider BEFORE any new work: an attempt that may have reached the provider is resolved from its recorded step, with the same key."""
    SqliteLedger(con, clock).mark_unconfirmed(run_id)
    if cfg is None:
        return
    from paypilot.payments.reconcile import reconcile_run          # lazy: payments imports core
    reconcile_run(con, run_id, cfg, clock, session_id)
