"""A4 done-when: the pipeline runs with stub logic; journal balanced; resume after a crash at any stage converges."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from paypilot.core import journal
from paypilot.core.crash import CRASH_POINTS
from paypilot.core.db import PersistenceFailure, connect, tx
from paypilot.core.history import SqliteHistory
from paypilot.schemas import ClientData
from tests import pipeline_stubs as st
from tests.fakes import fixed_clock

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def stubs(monkeypatch, tmp_path):
    monkeypatch.setenv("BANK_HMAC_KEY", "test-key-0123456789")
    st.install(monkeypatch)


def snapshot(db):
    con = connect(db)
    items = {r["source_ref"]: (r["state"], r["outcome"], r["version"]) for r in con.execute("select * from items")}
    return items, con


def test_end_to_end_bank_lines(tmp_path):
    db = str(tmp_path / "p.db"); st.write_scenario(tmp_path / "s")
    s = st.run(str(tmp_path / "s"), db)
    items, con = snapshot(db)
    got = {k.split("#")[1]: v[1] for k, v in items.items()}
    assert got == {"BL-1": "POSTED", "BL-2": "CANNOT_VERIFY", "BL-3": "HELD", "BL-4": "CANNOT_VERIFY", "BL-5": "POSTED"}
    assert s.counts == {"POSTED": 2, "CANNOT_VERIFY": 2, "HELD": 1} and s.status == "COMPLETE" and s.cost.cost_status == "KNOWN"
    # journal: one balanced entry per POSTED item, Dr expense / Cr Bank, trial balance sums to 0
    tb = journal.trial_balance(con)
    assert tb == {"Bank": -174000, "Staff meals": 174000} and sum(tb.values()) == 0
    assert con.execute("select count(*) from journal_entries").fetchone()[0] == 2
    # parse failure got a PARSE_FAILED decision + exception; held duplicate got a blocking HIGH exception
    pf = con.execute("select * from items where source_ref like '%BL-4'").fetchone()
    assert "PARSE_FAILED" in pf["decision_json"] and pf["decision_hash"]
    exc = {r["type"]: (r["severity"], r["status"]) for r in con.execute("select * from exceptions")}
    assert exc["DUPLICATE_TRANSACTION"] == ("HIGH", "CONTAINED") and exc["PARSE_FAILED"][0] == "MEDIUM"
    # evidence persisted for the posted items
    assert con.execute("select count(*) from item_evidence").fetchone()[0] >= 2
    # csv export
    paths = journal.write_journal_csv(con, s.run_id, tmp_path / "out")
    assert [p.name for p in paths] == ["journal.csv", "trial_balance.csv"] and "Staff meals" in paths[0].read_text()


def test_rerun_is_idempotent(tmp_path):
    db = str(tmp_path / "p.db"); st.write_scenario(tmp_path / "s")
    st.run(str(tmp_path / "s"), db); before, con = snapshot(db)
    n_audit = con.execute("select count(*) from audit_events").fetchone()[0]
    s2 = st.run(str(tmp_path / "s"), db); after, con = snapshot(db)
    assert before == after and con.execute("select count(*) from audit_events").fetchone()[0] == n_audit
    assert con.execute("select count(*) from runs").fetchone()[0] == 1 and s2.sessions == 2


def audit_chain_ok(con):
    for r in con.execute("select item_id, state from items"):
        ev = con.execute("select previous_state, new_state from audit_events where entity_type='item' and entity_id=? order by event_id",
                         (r["item_id"],)).fetchall()
        assert ev, "every item has audit events"
        assert all(a["new_state"] == b["previous_state"] for a, b in zip(ev, ev[1:])), f"broken chain for {r['item_id']}"
        assert ev[-1]["new_state"] == r["state"]


@pytest.mark.parametrize("point", ["after_ingest", "after_resolve", "after_check", "after_decide", "after_post_txn"])
def test_kill_at_any_stage_then_resume_converges(tmp_path, point):
    assert point in CRASH_POINTS
    st.write_scenario(tmp_path / "s")
    clean = str(tmp_path / "clean.db"); st.run(str(tmp_path / "s"), clean)
    crashed = str(tmp_path / "crashed.db")
    r = subprocess.run([sys.executable, "-m", "tests.pipeline_stubs", str(tmp_path / "s"), crashed], cwd=ROOT,
                       env={**os.environ, "PAYPILOT_CRASH_AT": point, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
    assert r.returncode == 137, r.stderr
    summary = st.run(str(tmp_path / "s"), crashed)                       # resume
    a, ca = snapshot(clean); b, cb = snapshot(crashed)
    assert {k: v[:2] for k, v in a.items()} == {k: v[:2] for k, v in b.items()}          # same final states + outcomes
    assert journal.trial_balance(ca) == journal.trial_balance(cb)
    assert cb.execute("select count(*) from journal_entries").fetchone()[0] == 2          # never a duplicate posting
    assert cb.execute("select count(*) from exceptions").fetchone()[0] == ca.execute("select count(*) from exceptions").fetchone()[0]
    audit_chain_ok(cb)
    # a session row exists only after ingest, so a kill at after_ingest leaves nothing to detect; every later kill does
    assert summary.recovered_from_crash is (point != "after_ingest")


# ---- journal rules ----
def test_journal_refuses_unbalanced_and_outside_transaction(tmp_path):
    from paypilot.schemas import JournalEntry, JournalLine
    from datetime import datetime, timezone
    con = connect(str(tmp_path / "j.db"))
    with tx(con):
        con.execute("INSERT INTO runs VALUES ('R','c','r','s',NULL,'h','p','d','{}','RUNNING','t','t')")
        con.execute("INSERT INTO items(item_id,client_id,run_id,kind,input_name,row_no,source_ref,operation,state,updated_at)"
                    " VALUES ('i','c','R','Invoice','f',1,'f#1','PAY_INVOICES','PAID','t')")
    e = lambda **kw: JournalEntry(entry_id="e1", item_id="i", kind="INVOICE_PAYMENT", decision_hash="h", currency="INR", memo="m",
                                  posted_at=datetime.now(timezone.utc), vendor_id="V1", approval_id="a", attempt_key="k:a1",
                                  lines=[JournalLine(line_no=1, account="Accounts payable - V1", debit_paise=500),
                                         JournalLine(line_no=2, account=journal.CLEARING, credit_paise=500)], **kw)
    with pytest.raises(RuntimeError):
        journal.post_entry(con, e())
    bad = e().model_copy(update={"lines": [JournalLine(line_no=1, account="A", debit_paise=1, credit_paise=1),
                                           JournalLine(line_no=2, account="B", credit_paise=0)]})
    with pytest.raises(journal.JournalError):
        with tx(con):
            journal.post_entry(con, bad)
    with tx(con):
        journal.post_entry(con, e())
    with pytest.raises(PersistenceFailure):                               # UNIQUE(item_id, kind): never two payment entries
        with tx(con):
            journal.post_entry(con, e().model_copy(update={"entry_id": "e2"}))
    rev = journal.reversal_of_payment(con, "i", fixed_clock())
    with tx(con):
        journal.post_entry(con, rev)
    assert journal.trial_balance(con) == {"Accounts payable - V1": 0, journal.CLEARING: 0}      # reversal nets to zero


# ---- history ----
def seed(con, rows):
    with tx(con):
        con.execute("INSERT OR IGNORE INTO runs VALUES ('R1','northbay','r','s1',NULL,'h','p','d','{}','RUNNING','t','t')")
        con.execute("INSERT OR IGNORE INTO runs VALUES ('R2','northbay','r','s2',NULL,'h','p','d','{}','RUNNING','t','t')")
        for r in rows:
            con.execute("INSERT INTO items(item_id,client_id,run_id,kind,input_name,row_no,source_ref,operation,state,vendor_id,"
                        "invoice_no_canonical,descriptor_key,amount_paise,currency,item_date,transaction_fingerprint,payload_json,updated_at)"
                        " VALUES (:item_id,'northbay',:run,:kind,:input,:row,:ref,'PAY_INVOICES',:state,:vendor,:inv,:desc,:amt,'INR',"
                        ":date,:fp,:payload,'t')", {"kind": "Invoice", "state": "AWAITING_APPROVAL", "vendor": "V1", "inv": "INV1",
                                                     "desc": None, "amt": 100, "date": "2026-11-01", "fp": None, "payload": "{}", **r})


def test_history_same_run_ordering_and_cross_run(tmp_path):
    con = connect(str(tmp_path / "h.db"))
    seed(con, [dict(item_id="a", run="R1", input="f", row=1, ref="f#A"), dict(item_id="b", run="R1", input="f", row=2, ref="f#B"),
               dict(item_id="c", run="R2", input="f", row=1, ref="f#C")])
    h = SqliteHistory(con, ClientData(client_id="northbay", vendors=[]), "northbay", "R1")
    assert [p.ref for p in h.prior_by_invoice("V1", "INV1", before_item="a")] == ["f#C"]          # first of the run: only older runs
    got = h.prior_by_invoice("V1", "INV1", before_item="b")
    assert [(p.ref, p.source) for p in got] == [("f#A", "this_run"), ("f#C", "db_history")]       # later item sees the first
    assert h.prior_by_invoice("V1", "NOPE", before_item="b") == [] and h.prior_by_invoice("V2", "INV1", before_item="b") == []
    assert len(h.prior_by_invoice("V1", "INV1", before_item=None)) == 3


def test_history_txn_fingerprint_payment_history_po_and_containment(tmp_path):
    from datetime import date
    from paypilot.core import ids
    from paypilot.schemas import HistoryRecord
    con = connect(str(tmp_path / "h.db"))
    seed(con, [dict(item_id="a", run="R1", input="f", row=1, ref="f#A", desc="SWIGGY", fp="fp1", kind="BankLine", state="POSTED"),
               dict(item_id="p1", run="R1", input="f", row=2, ref="f#P1", payload='{"po_number": "PO1"}', amt=700),
               dict(item_id="p2", run="R1", input="f", row=3, ref="f#P2", payload='{"po_number": "PO1"}', amt=300, state="HELD"),
               dict(item_id="x", run="R1", input="f", row=9, ref="f#X")])
    hist = [HistoryRecord(ref="H1", vendor_id="V1", invoice_no_canonical="OLD1", amount_paise=5, currency="INR",
                          paid_on=date(2026, 1, 1), bank_reference="UTR9", status="PAID", record_ref="ph#H1")]
    h = SqliteHistory(con, ClientData(client_id="northbay", vendors=[], history=hist), "northbay", "R1")
    assert [p.ref for p in h.prior_by_txn("SWIGGY", 100, "INR", date(2026, 10, 30), date(2026, 11, 2), before_item="x")] == ["f#A"]
    assert h.prior_by_txn("SWIGGY", 100, "INR", date(2026, 1, 1), date(2026, 1, 2), before_item="x") == []
    assert [p.ref for p in h.prior_by_fingerprint("fp1", before_item="x")] == ["f#A"]
    assert [p.source for p in h.prior_by_fingerprint(ids.sha256_hex("ref", "UTR9"), before_item=None)] == ["payment_history"]
    assert [p.source for p in h.prior_by_invoice("V1", "OLD1", before_item=None)] == ["payment_history"]
    assert h.po_billed_paise("PO1", before_item="x") == 700                         # the HELD invoice does not count
    assert h.po_billed_paise("PO1", before_item="p1") == 0                           # nor does the item itself / later ones
    assert h.open_critical_for_vendor("V1") == []
    with tx(con):
        con.execute("INSERT INTO exceptions(exception_id,run_id,item_id,vendor_id,type,severity,status,blocking,summary,details,"
                    "recommended_action,evidence_refs_json,related_refs_json,origin_key,created_at,updated_at) VALUES "
                    "('e1','R1','a','V1','PAYMENT_RETURNED','CRITICAL','CONTAINED',1,'s','d','r','[]','[]','o','t','t')")
    assert h.open_critical_for_vendor("V1") == ["e1"] and h.open_critical_for_vendor("V2") == []
    with tx(con):
        con.execute("UPDATE exceptions SET status='RESOLVED'")
    assert h.open_critical_for_vendor("V1") == []


def test_unsupported_operation_run_is_not_a_crash(tmp_path):
    st.write_scenario(tmp_path / "s")
    import json
    p = tmp_path / "s" / "request.json"; d = json.loads(p.read_text()); d["operation"] = "SING_A_SONG"; p.write_text(json.dumps(d))
    s = st.run(str(tmp_path / "s"), str(tmp_path / "u.db"))
    assert s.counts == {"NEW": 5} and s.operation is None            # nothing decided; A13 adds the run-level CANNOT_VERIFY result
