"""Audit events. Owner: lane A. Written on the CALLER's connection so the state change and its event share one transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


def append_event(con: sqlite3.Connection, *, run_id: str, session_id: str, entity_type: str, entity_id: str,
                 previous_state: str | None, new_state: str | None, actor: str, ts: datetime,
                 reason: str | None = None, metadata: dict[str, Any] | None = None) -> int:
    """Must be called inside `tx(con)`; never opens its own transaction."""
    if not con.in_transaction:
        raise RuntimeError("audit.append_event must run inside the state-change transaction")
    cur = con.execute(
        "INSERT INTO audit_events(run_id, session_id, entity_type, entity_id, previous_state, new_state, actor, reason,"
        " metadata_json, ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, session_id, entity_type, entity_id, previous_state, new_state, actor, reason,
         json.dumps(metadata or {}, sort_keys=True, default=str), ts.isoformat()))
    return int(cur.lastrowid)
