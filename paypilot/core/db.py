"""SQLite access (INTERFACES §6). Owner: lane A.

Rule: any failed write is a PersistenceFailure -> CLI exit 70, raised BEFORE any provider call.
`tx()` is one transaction: state change + audit event + journal go through the same connection.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
EXIT_PERSISTENCE = 70


class PersistenceFailure(Exception):
    """A write failed. Never continue to a provider call; exit 70."""


def connect(db_path: str | Path, *, apply_schema: bool = True) -> sqlite3.Connection:
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), isolation_level=None, timeout=10)  # autocommit; we manage BEGIN explicitly
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    if apply_schema:
        has = con.execute("select 1 from sqlite_master where type='table' and name='runs'").fetchone()
        if not has:
            con.executescript(SCHEMA_PATH.read_text())
    return con


@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT. Any error rolls back; sqlite errors become PersistenceFailure."""
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        raise PersistenceFailure(f"cannot begin transaction: {e}") from e
    try:
        yield con
        con.execute("COMMIT")
    except sqlite3.Error as e:
        _rollback(con)
        raise PersistenceFailure(f"write failed: {e}") from e
    except BaseException:
        _rollback(con)
        raise


def _rollback(con: sqlite3.Connection) -> None:
    try:
        con.execute("ROLLBACK")
    except sqlite3.Error:
        pass
