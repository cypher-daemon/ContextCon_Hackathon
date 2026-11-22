"""Every SQLite connection a lane-A test opens is closed at teardown, so a leak can never surface as a stray ResourceWarning
(which Python attributes to whichever unrelated test happens to be running when the object is garbage-collected)."""
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _close_sqlite_connections(monkeypatch):
    opened: list[sqlite3.Connection] = []
    real = sqlite3.connect

    def tracking(*args, **kwargs):
        con = real(*args, **kwargs)
        opened.append(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", tracking)
    yield
    for con in opened:
        try:
            con.close()
        except sqlite3.Error:
            pass
