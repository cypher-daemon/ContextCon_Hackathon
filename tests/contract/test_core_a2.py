"""A2 done-when: illegal/stale transitions raise; the audit row is atomic with the state change."""
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from paypilot.core import crash
from paypilot.core.db import connect, tx
from paypilot.core.state import ALLOWED, FINAL, IllegalTransition, StaleVersion, transition
from paypilot.schemas import ItemState as S

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def _db(path=":memory:"):
    con = connect(path)
    with tx(con):
        con.execute("INSERT INTO runs VALUES ('R','c','r','s',NULL,'h','p','d','{}','RUNNING','t','t')")
        con.execute("INSERT INTO items(item_id,client_id,run_id,kind,input_name,row_no,source_ref,operation,state,updated_at)"
                    " VALUES ('i','c','R','Invoice','invoices.json',1,'invoices.json#INV-1','PAY_INVOICES','NEW','t')")
    return con


def step(con, frm, ver, to, **kw):
    return transition(con, item_id="i", run_id="R", session_id="S", expected_state=frm, expected_version=ver,
                      new_state=to, actor="t", ts=NOW, **kw)


def test_happy_path_bumps_version_and_audits_each_step():
    con = _db()
    v = 0
    path = [S.PARSED, S.RESOLVED, S.CHECKED, S.DECIDED, S.AWAITING_APPROVAL, S.APPROVED, S.PAYMENT_IN_PROGRESS, S.PAID]
    prev = S.NEW
    for s in path:
        v = step(con, prev, v, s); prev = s
    row = con.execute("select state,version,outcome from items").fetchone()
    assert (row["state"], row["version"], row["outcome"]) == ("PAID", 8, "PAID")
    assert con.execute("select count(*) from audit_events").fetchone()[0] == 8


def test_illegal_transition_raises_and_changes_nothing():
    con = _db()
    with pytest.raises(IllegalTransition):
        step(con, S.NEW, 0, S.PAID)
    assert con.execute("select state,version from items").fetchone()[:] == ("NEW", 0)
    assert con.execute("select count(*) from audit_events").fetchone()[0] == 0


def test_stale_version_is_refused_and_second_writer_loses():
    a = _db()
    step(a, S.NEW, 0, S.PARSED)
    with pytest.raises(StaleVersion):
        step(a, S.NEW, 0, S.PARSED)                       # replay of a stale read
    with pytest.raises(StaleVersion):
        step(a, S.PARSED, 0, S.RESOLVED)                  # right state, stale version


def test_two_connections_race_only_one_wins(tmp_path):
    db = str(tmp_path / "t.db"); c1 = _db(db); c2 = connect(db)
    step(c1, S.NEW, 0, S.PARSED)
    wins = 0
    for c in (c1, c2):                                    # both read (PARSED, v1) and try to advance
        try:
            step(c, S.PARSED, 1, S.RESOLVED); wins += 1
        except StaleVersion:
            pass
    assert wins == 1


def test_audit_failure_rolls_back_the_state_change(tmp_path):
    con = _db()
    con.execute("DROP TABLE audit_events")                # make the audit insert fail
    with pytest.raises(Exception):
        step(con, S.NEW, 0, S.PARSED)
    assert con.execute("select state,version from items").fetchone()[:] == ("NEW", 0)


def test_joins_callers_transaction_and_rolls_back_with_it():
    con = _db()
    with pytest.raises(RuntimeError):
        with tx(con):
            step(con, S.NEW, 0, S.PARSED)
            raise RuntimeError("later step in the same txn fails")
    assert con.execute("select state from items").fetchone()[0] == "NEW"
    assert con.execute("select count(*) from audit_events").fetchone()[0] == 0


def test_columns_whitelisted():
    con = _db()
    with pytest.raises(ValueError):
        step(con, S.NEW, 0, S.PARSED, set_columns={"state='PAID', x": 1})
    step(con, S.NEW, 0, S.PARSED, set_columns={"vendor_id": "V1"})
    assert con.execute("select vendor_id from items").fetchone()[0] == "V1"


def test_map_is_complete_and_final_states_are_closed():
    assert set(ALLOWED) == set(S)
    assert all(not ALLOWED[s] for s in FINAL)
    assert ALLOWED[S.PAID] == {S.HELD}                    # the one exit: reversal after success
    assert S.PAID not in {t for frm, to in ALLOWED.items() for t in to if frm not in (S.PAYMENT_IN_PROGRESS, S.PAYMENT_UNCERTAIN)}


def test_crash_point_exits_137_only_at_the_named_point():
    code = "from paypilot.core.crash import crash_point as c\nc('after_ingest')\nc('after_post_txn')\nprint('survived')"
    env = {**os.environ, "PAYPILOT_CRASH_AT": "after_post_txn"}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 137 and "survived" not in r.stdout
    r = subprocess.run([sys.executable, "-c", code], env={k: v for k, v in os.environ.items() if k != "PAYPILOT_CRASH_AT"},
                       capture_output=True, text=True)
    assert r.returncode == 0 and "survived" in r.stdout


def test_typo_in_crash_env_is_rejected(monkeypatch):
    monkeypatch.setenv("PAYPILOT_CRASH_AT", "after_postt_txn")
    with pytest.raises(ValueError):
        crash.validate_env()
    with pytest.raises(ValueError):
        crash.crash_point("nope")
