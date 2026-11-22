"""Dashboard (scripts/dashboard.py) builds its view from the DB, read-only, scoped to the chosen run."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from paypilot.core.db import connect

SPEC = importlib.util.spec_from_file_location("dashboard", Path(__file__).resolve().parents[2] / "scripts" / "dashboard.py")
dash = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dash)

T = "2026-11-22T08:30:00+00:00"


def _run(con, run_id):
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, "c1", "rq-" + run_id, "sha-" + run_id, "PAY_INVOICES", "ch", "ph", "dh", "{}", "DONE", T, T))


def _item(con, run_id, item_id, state, amount, *, kind="Invoice", row=1, payload=None):
    con.execute("INSERT INTO items (item_id,client_id,run_id,kind,input_name,row_no,source_ref,operation,state,amount_paise,"
                "vendor_id,payload_json,decision_json,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item_id, "c1", run_id, kind, "in.json", row, f"in.json#{item_id}", "PAY_INVOICES", state, amount, "V-1",
                 json.dumps(payload or {"invoice_id": item_id, "vendor_name_raw": "Acme Ltd"}),
                 json.dumps({"proposal": "HELD" if state == "HELD" else "AWAITING_APPROVAL", "flags": [], "reasons": []}), T))


def _pay(con, item_id, status):
    con.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("ap-" + item_id, item_id, "h" * 64, "APPROVED", "Controller", None, "[]", T, T, 1))
    con.execute("INSERT INTO payments (payment_id,item_id,approval_id,payment_key,vendor_id,provider,amount_paise,currency,status,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("pay-" + item_id, item_id, "ap-" + item_id, "pp_" + item_id, "V-1", "fake", 1000, "INR", status, T, T))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "pp.db"
    con = connect(path)
    _run(con, "R1"); _run(con, "R2")
    _item(con, "R1", "paid1", "PAID", 5000, row=1);       _pay(con, "paid1", "PROVIDER_CONFIRMED")
    _item(con, "R1", "ret1", "HELD", 3000, row=2);        _pay(con, "ret1", "RETURNED")
    _item(con, "R1", "held1", "HELD", 2000, row=3)
    _item(con, "R2", "other", "HELD", 700, row=1)
    for ex_id, run, status, blocking in [("e1", "R1", "CONTAINED", 1), ("e2", "R1", "RESOLVED", 1),
                                         ("e3", "R1", "FALSE_POSITIVE", 1), ("e4", "R2", "CONTAINED", 1)]:
        con.execute("INSERT INTO exceptions (exception_id,run_id,item_id,type,severity,status,blocking,summary,details,"
                    "recommended_action,evidence_refs_json,related_refs_json,origin_key,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ex_id, run, "held1" if run == "R1" else "other", "T" + ex_id, "HIGH", status, blocking, "s", "d", "a",
                     "[]", "[]", ex_id, T, T))
    for run, item in [("R1", "held1"), ("R2", "other")]:
        con.execute("INSERT INTO journal_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("je-" + item, item, "BANK_LINE_POSTING", "V-1", "h", None, None, "INR", "m", T))
        con.execute("INSERT INTO journal_lines VALUES (?,?,?,?,?)", ("je-" + item, 1, "Expense", 100, 0))
        con.execute("INSERT INTO journal_lines VALUES (?,?,?,?,?)", ("je-" + item, 2, "Bank", 0, 100))
        con.execute("INSERT INTO audit_events (run_id,session_id,entity_type,entity_id,previous_state,new_state,actor,metadata_json,ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (run, "s", "item", item, "CHECKED", "HELD", "system", "{}", T))
    # one priced call and one with a NULL price
    for key, actual in [("k1", 0.5), ("k2", None)]:
        con.execute("INSERT INTO cost_ledger VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "R1", "s", None, "st", "ifsc", "op", "call", 1, None, actual, "CONFIRMED", 200, 1, key, 1, T))
    con.close()
    return path


def test_money_buckets_never_overlap(db):
    s = dash.build_state(db, "R1")
    assert s["money"] == {"paid_paise": 5000, "held_paise": 2000, "returned_paise": 3000}


def test_run_scope_applies_to_every_panel(db):
    s = dash.build_state(db, "R1")
    assert {i["run_id"] for i in s["items"]} == {"R1"}
    assert {e["run_id"] for e in s["exceptions"]} == {"R1"}
    assert {e["run_id"] for e in s["audit"]} == {"R1"}
    assert sum(r["debit"] for r in s["trial_balance"]) == 100       # only R1's journal entry
    assert sum(r["debit"] for r in dash.build_state(db, None)["trial_balance"]) == 200


def test_only_unclosed_exceptions_are_open(db):
    c = dash.build_state(db, "R1")["counts"]
    assert c["open_exceptions"] == 1 and c["blocking"] == 1          # RESOLVED and FALSE_POSITIVE are closed


def test_unpriced_call_is_unknown_never_zero(db):
    cost = dash.build_state(db, "R1")["cost"]
    assert cost["status"] == "PARTIAL"                               # one priced, one not: total is a lower bound
    assert cost["total"] == 0.5
    assert [(p["provider"], p["unknown"]) for p in cost["providers"]] == [("ifsc", True)]   # provider total is UNKNOWN, not 0.5


def test_database_is_opened_read_only(db):
    con = dash.connect(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM items")
    con.close()


def test_journal_is_scoped_to_the_run_and_carries_its_lines(db):
    j = dash.build_state(db, "R1")["journal"]
    assert [e["item_id"] for e in j] == ["held1"]
    assert [(l["account"], l["debit_paise"], l["credit_paise"]) for l in j[0]["lines"]] == [("Expense", 100, 0), ("Bank", 0, 100)]
    assert len(dash.build_state(db, None)["journal"]) == 2


def test_pages_and_assets_are_served_and_nothing_else(db):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.make_handler(db))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def status(path):
        try:
            return urllib.request.urlopen(base + path).status
        except urllib.error.HTTPError as e:
            return e.code
    try:
        for ok in ("/", "/payments", "/exceptions", "/approvals", "/books", "/static/app.js", "/static/app.css", "/api/state"):
            assert status(ok) == 200, ok
        for bad in ("/static/../dashboard.py", "/static/%2e%2e/dashboard.py", "/etc/passwd", "/nope"):
            assert status(bad) == 404, bad
    finally:
        srv.shutdown(); srv.server_close()
