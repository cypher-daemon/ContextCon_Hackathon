"""Item state machine (ARCHITECTURE §6). Owner: lane A.

Every transition is a compare-and-swap on (state, version) with its audit event in the SAME transaction.
Illegal transition -> IllegalTransition. Lost race / stale caller -> StaleVersion. Neither changes anything.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from paypilot.core.audit import append_event
from paypilot.core.db import tx
from paypilot.schemas import ItemState as S, Outcome

ALLOWED: dict[S, frozenset[S]] = {
    S.NEW: frozenset({S.PARSED, S.CANNOT_VERIFY, S.STALE}),           # NEW -> CANNOT_VERIFY: ParseFailure
    S.PARSED: frozenset({S.RESOLVED, S.CANNOT_VERIFY, S.STALE}),
    S.RESOLVED: frozenset({S.CHECKED, S.STALE}),
    S.CHECKED: frozenset({S.DECIDED, S.STALE}),
    S.DECIDED: frozenset({S.POSTED, S.AWAITING_APPROVAL, S.HELD, S.CANNOT_VERIFY, S.STALE}),
    S.AWAITING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.HELD, S.STALE}),   # ->HELD: vendor contained at enqueue
    S.HELD: frozenset({S.APPROVED, S.REJECTED, S.STALE}),                        # ->APPROVED: overridable flags only + reason
    S.APPROVED: frozenset({S.PAYMENT_IN_PROGRESS, S.HELD}),                      # ->HELD: bank account changed before execution (refused, no provider call)
    S.PAYMENT_IN_PROGRESS: frozenset({S.PAID, S.PAYMENT_UNCERTAIN, S.FAILED}),
    S.PAYMENT_UNCERTAIN: frozenset({S.PAID, S.FAILED}),                          # reconcile evidence only
    S.FAILED: frozenset({S.HELD}),                                               # fresh checks + new approval before retry
    S.PAID: frozenset({S.HELD}),                                                 # ONLY a reversal after success; never paid again
    S.STALE: frozenset({S.PARSED}),                                              # re-decided from the top
    S.POSTED: frozenset(), S.REJECTED: frozenset(), S.CANNOT_VERIFY: frozenset(),
}
FINAL = frozenset({S.POSTED, S.REJECTED, S.CANNOT_VERIFY})                       # never re-run (PAID's one exit is the reversal)
_STATE_OUTCOME = {s: Outcome(s.value) for s in S if s.value in Outcome._value2member_map_}
# columns a transition may set alongside the state (whitelist: keys are interpolated into SQL)
UPDATABLE = frozenset({"payload_json", "parse_errors_json", "transaction_fingerprint", "invoice_no_canonical", "descriptor_key",
                       "vendor_id", "amount_paise", "currency", "item_date", "resolution_json", "checks_json",
                       "decision_json", "decision_hash"})


class IllegalTransition(Exception):
    """Transition not in ALLOWED."""


class StaleVersion(Exception):
    """CAS miss: the item is not at the (state, version) the caller read."""


def can_transition(frm: S, to: S) -> bool:
    return to in ALLOWED[frm]


def transition(con: sqlite3.Connection, *, item_id: str, run_id: str, session_id: str, expected_state: S,
               expected_version: int, new_state: S, actor: str, ts: datetime, reason: str | None = None,
               metadata: dict[str, Any] | None = None, set_columns: dict[str, Any] | None = None) -> int:
    """CAS the item to `new_state`; returns the new version. Joins the caller's transaction if one is open."""
    if not can_transition(expected_state, new_state):
        raise IllegalTransition(f"{expected_state.value} -> {new_state.value} is not allowed")
    cols = set_columns or {}
    bad = set(cols) - UPDATABLE
    if bad:
        raise ValueError(f"not updatable via transition: {sorted(bad)}")

    def _do(c: sqlite3.Connection) -> int:
        sets = ", ".join(f"{k}=?" for k in cols)
        cur = c.execute(
            "UPDATE items SET state=?, version=version+1, outcome=?, updated_at=?" + (", " + sets if sets else "") +
            " WHERE item_id=? AND state=? AND version=?",
            (new_state.value, (_STATE_OUTCOME[new_state].value if new_state in _STATE_OUTCOME else None), ts.isoformat(),
             *cols.values(), item_id, expected_state.value, expected_version))
        if cur.rowcount != 1:
            raise StaleVersion(f"item {item_id} is no longer at {expected_state.value} v{expected_version}")
        append_event(c, run_id=run_id, session_id=session_id, entity_type="item", entity_id=item_id,
                     previous_state=expected_state.value, new_state=new_state.value, actor=actor, ts=ts,
                     reason=reason, metadata=metadata)
        return expected_version + 1

    if con.in_transaction:
        return _do(con)
    with tx(con):
        return _do(con)
