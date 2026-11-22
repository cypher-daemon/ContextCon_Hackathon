"""payments.reconcile (A8): the reconcile-first gate. First thing on every run / resume / approve. Owner: lane A.

For every payment of the run: resume a half-done attempt from its recorded step (never a new key, never a create because a search
came back empty), re-poll non-terminal ones, resolve PAYMENT_UNCERTAIN by looking the SAME key up, and watch confirmed payments for a
reversal after success. A payment still CREATED (approved, nothing sent yet) is submitted here, so `resume` finishes it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Callable

from paypilot.context import StageContext
from paypilot.core.db import connect
from paypilot.core.payments_repo import get_payment
from paypilot.payments.executor import _Ctx, _advance
from paypilot.payments.provider import build_provider
from paypilot.schemas import Payment, RunConfig


def reconcile_run(con: sqlite3.Connection, run_id: str, cfg: RunConfig, clock: Callable[[], datetime], session_id: str = "recovery",
                  provider=None) -> list[str]:
    """Returns the payment_ids it looked at."""
    rows = con.execute("SELECT p.payment_id, p.provider FROM payments p JOIN items i ON i.item_id=p.item_id WHERE i.run_id=? "
                       "ORDER BY p.created_at, p.payment_id", (run_id,)).fetchall()
    cache: dict[str, object] = {}
    seen: list[str] = []
    for r in rows:
        prov = provider or cache.setdefault(r["provider"], build_provider(r["provider"], cfg))
        _advance(_Ctx(con, prov, cfg, clock, session_id), r["payment_id"])
        seen.append(r["payment_id"])
    return seen


def reconcile_open(provider, ctx: StageContext) -> list[Payment]:
    """First thing on every run/resume/approve (INTERFACES §9)."""
    con = connect(ctx.cfg.storage["db_path"], apply_schema=False)
    try:
        return [get_payment(con, pid) for pid in reconcile_run(con, ctx.run_id, ctx.cfg, ctx.clock, ctx.session_id, provider)]
    finally:
        con.close()
