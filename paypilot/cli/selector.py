"""Item selector (INTERFACES §10, task A5b). Owner: lane A.

`ITEM` is the exact item_id, or a source identifier (invoice_id / line_id / message_id) matched EXACTLY within the run.
Zero or several matches -> SelectorError listing candidates (CLI exit 2). It never picks one.
"""
from __future__ import annotations

import sqlite3


class SelectorError(Exception):
    """Unknown or ambiguous ITEM -> CLI exit 2."""


def latest_run_id(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT run_id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    if row is None:
        raise SelectorError("no runs in the database yet; run one first")
    return row["run_id"]


def resolve_run(con: sqlite3.Connection, run_id: str | None) -> str:
    """--run RUN_ID, or the most recent run."""
    if run_id is None:
        return latest_run_id(con)
    if con.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
        raise SelectorError(f"unknown run {run_id!r}")
    return run_id


def resolve_item(con: sqlite3.Connection, run_id: str, selector: str) -> sqlite3.Row:
    exact = con.execute("SELECT * FROM items WHERE run_id=? AND item_id=?", (run_id, selector)).fetchone()
    if exact is not None:
        return exact
    rows = [r for r in con.execute("SELECT * FROM items WHERE run_id=? ORDER BY input_name, row_no", (run_id,))
            if "#" in r["source_ref"] and r["source_ref"].split("#", 1)[1] == selector]
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise SelectorError(f"no item {selector!r} in run {run_id}")
    cands = "; ".join(f"{r['item_id']} ({r['source_ref']})" for r in rows)
    raise SelectorError(f"{selector!r} is ambiguous in run {run_id}: {cands}. Use the exact item_id.")
