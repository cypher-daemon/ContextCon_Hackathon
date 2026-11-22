"""A7 done-when: double approve = no-op; override refused; stale refused; decline -> new approval (seq 2) -> attempt a2 linked to it.
All through the CLI against a real database; lane B/C logic is replaced by the scripted stand-ins in tests/pipeline_stubs.py."""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from paypilot.cli.app import app
from paypilot.core import ids, journal
from paypilot.core.db import connect
from paypilot.core.lock import RunLock
from tests import pipeline_stubs as st

ROOT = Path(__file__).resolve().parents[2]
VM = ("vendor_id,legal_name,aliases,cin,gstin,domain,bank_account,ifsc,default_account,category,last_updated\n"
      "V1,Swiggy Limited,SWIGGY,,,swiggy.com,000000000001,,Staff meals,food,2026-09-30\n"
      "V2,Delhivery Limited,DELHIVERY,,,delhivery.com,000000000003,,Freight - carriers,freight,2026-09-30\n")
POLICY = "approvals:\n  approvers:\n    - Finance Controller\n    - AP Manager\n"
FC, AP = "Finance Controller", "AP Manager"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_HMAC_KEY", "test-key-0123456789")
    monkeypatch.setenv("PAYPILOT_ROOT", str(tmp_path))
    monkeypatch.delenv("PAYPILOT_CRASH_AT", raising=False)
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["payments"]["approve_executes_inline"] = False          # execution is A8; these tests are about the approval + enqueue
    cfg["approvals"]["lock_wait_s"] = 0.2
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    c = tmp_path / "clients" / "northbay"; c.mkdir(parents=True)
    (c / "vendor_master.csv").write_text(VM); (c / "policy.yaml").write_text(POLICY)
    st.install(monkeypatch)
    st.write_invoice_scenario(tmp_path / "scenarios" / "INV"); st.write_scenario(tmp_path / "scenarios" / "BANK")
    assert cli("run", "--scenario", str(tmp_path / "scenarios/INV")).exit_code == 0
    return tmp_path


def cli(*args):
    return CliRunner().invoke(app, list(args))


def db(world):
    return connect(str(world / "var/paypilot.db"))


def item(con, ref):
    return con.execute("select * from items where source_ref like ?", (f"%#{ref}",)).fetchone()


def counts(con):
    return {t: con.execute(f"select count(*) from {t}").fetchone()[0]
            for t in ("approvals", "payments", "payment_attempts", "journal_entries", "audit_events", "exceptions")}


def refused(r, code):
    return r.exit_code == 2 and f"refused ({code})" in (r.stderr or r.output)


# ---------------- the queue ----------------
def test_queue_shows_what_can_be_acted_on_with_the_evidence_to_decide(world):
    from paypilot.approvals.service import approval_queue
    con = db(world); run_id = con.execute("select run_id from runs").fetchone()[0]
    rows, blocked = approval_queue(con, run_id)
    assert [r["ref"] for r in rows] == ["INV-1", "INV-2", "INV-4"] and blocked == 2         # INV-3/INV-6 are never-overridable; INV-5 is CANNOT_VERIFY
    inv1, inv2 = rows[0], rows[1]
    assert (inv1["payee"], inv1["vendor_id"], inv1["amount_paise"], inv1["account"], inv1["account_last4"]) == ("Delhivery Limited", "V2", 4800000, "Freight - carriers", "0003")
    assert inv1["bank_status"] == {"ifsc_valid": "PASS", "account_matches_master": "PASS", "ownership_verified": "NOT_VERIFIED"}   # ownership never claimed
    assert inv1["flags"] == [] and inv2["flags"] == ["PO_AMOUNT_MISMATCH"] and inv2["overridable"] == ["PO_AMOUNT_MISMATCH"]
    assert inv1["evidence"] and inv1["evidence"][0].startswith("vendor_master:") and inv1["open_exceptions"] == 0
    r = cli("approvals")
    flat = " ".join(r.output.split())
    assert r.exit_code == 0 and all(ref in flat for ref in ("INV-1", "INV-2", "INV-4"))
    assert not any(ref in flat for ref in ("INV-3", "INV-5", "INV-6"))
    assert "3 item(s) can be acted on" in flat and "2 held item(s) cannot be approved" in flat
    for needle in ("IFSC PASS", "Owner NOT_VERIFIED", "PO_AMOUNT_MISMATCH", "₹48,000.00"):
        assert needle in flat, needle


# ---------------- approve: the enqueue transaction ----------------
def test_approve_binds_the_hash_and_enqueues_everything_in_one_go(world):
    r = cli("approve", "INV-1", "--by", FC)
    assert r.exit_code == 0 and "approved" in r.output and "CREATED" in r.output, r.output + str(r.exception)
    con = db(world); it = item(con, "INV-1")
    ap = con.execute("select * from approvals").fetchone()
    assert (ap["status"], ap["reviewer"], ap["approval_seq"], ap["decision_hash"]) == ("APPROVED", FC, 1, it["decision_hash"])
    assert ap["approval_id"] == ids.approval_id(it["item_id"], it["decision_hash"], 1) and json.loads(ap["overrides_json"]) == []
    assert it["state"] == "APPROVED"
    pay = con.execute("select * from payments").fetchone()
    pk = ids.payment_key("northbay", "V2", it["invoice_no_canonical"], 4800000, "INR")
    assert (pay["payment_key"], pay["status"], pay["current_attempt"], pay["provider"], pay["approval_id"]) == (pk, "CREATED", 1, "fake", ap["approval_id"])
    att = con.execute("select * from payment_attempts").fetchone()
    assert (att["attempt_key"], att["step"], att["outcome"], att["approval_id"], att["provider_ref"]) == (f"{pk}:a1", "NONE", "PENDING", ap["approval_id"], None)
    je = con.execute("select * from journal_entries").fetchone()
    assert (je["kind"], je["approval_id"], je["decision_hash"], je["vendor_id"]) == ("INVOICE_ACCRUAL", ap["approval_id"], it["decision_hash"], "V2")
    assert journal.trial_balance(con) == {"Accounts payable - V2": -4800000, "Freight - carriers": 4800000}      # Dr expense / Cr AP
    kinds = {(e["entity_type"], e["new_state"]) for e in con.execute("select * from audit_events")}
    assert {("approval", "APPROVED"), ("payment", "CREATED"), ("item", "APPROVED")} <= kinds
    out = next((world / "out").iterdir()) / "payment_instructions.csv"
    rows = list(csv.DictReader(out.read_text().splitlines()))
    assert len(rows) == 1 and (rows[0]["do_not_execute"], rows[0]["status"], rows[0]["provider"], rows[0]["provider_ref"]) == ("TRUE", "CREATED", "fake", "")
    with pytest.raises(Exception):                                                       # the payment key never contains an approval id
        assert ap["approval_id"] in pk


def test_double_approve_is_a_no_op_even_from_another_approver(world):
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    con = db(world); before = counts(con)
    for who in (FC, AP):
        r = cli("approve", "INV-1", "--by", who)
        assert r.exit_code == 0 and f"already approved by {FC}" in " ".join(r.output.split()) and "nothing changed" in r.output
    assert counts(con) == before and con.execute("select count(*) from payments").fetchone()[0] == 1


def test_two_invoices_can_be_approved_one_after_the_other(world):
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    assert cli("approve", "INV-4", "--by", AP).exit_code == 0                            # a CREATED payment (no provider call yet) needs no reconcile
    con = db(world)
    assert [r["state"] for r in con.execute("select state from items where state='APPROVED'")] == ["APPROVED", "APPROVED"]
    assert counts(con)["payments"] == 2 and journal.trial_balance(con)["Freight - carriers"] == 4800000 + 1200000


# ---------------- who may approve ----------------
def test_only_allowlisted_approvers(world):
    con = db(world); before = counts(con)
    assert refused(cli("approve", "INV-1", "--by", "Mallory"), "NOT_AN_APPROVER")
    assert refused(cli("approve", "INV-1", "--by", "finance controller"), "NOT_AN_APPROVER")          # exact name, not "close enough"
    assert refused(cli("approve", "INV-1", "--by", " "), "INVALID_REVIEWER")
    assert refused(cli("approve", "INV-1", "--by", "Finance\x1b[31m Controller"), "INVALID_REVIEWER")
    assert refused(cli("approve", "INV-1", "--by", "Finance Controller‮"), "INVALID_REVIEWER")
    assert counts(con) == before and item(con, "INV-1")["state"] == "AWAITING_APPROVAL"
    assert cli("approve", "INV-1", "--by", f"  {FC}  ").exit_code == 0                                  # surrounding whitespace is trimmed
    assert con.execute("select reviewer from approvals").fetchone()[0] == FC


def test_empty_allowlist_means_any_declared_name(world):
    (world / "clients/northbay/policy.yaml").write_text("po:\n  tolerance_pct: 1.0\n")
    assert cli("run", "--scenario", str(world / "scenarios/INV")).exit_code == 0          # policy hash unchanged by approvers; same run
    assert cli("approve", "INV-1", "--by", "Anyone At All").exit_code == 0


# ---------------- overrides ----------------
def test_never_overridable_is_refused_with_or_without_override(world):
    con = db(world); before = counts(con)
    assert refused(cli("approve", "INV-3", "--by", FC), "NEVER_OVERRIDABLE")
    assert refused(cli("approve", "INV-3", "--by", FC, "--override", "DUPLICATE_INVOICE", "--reason", "trust me"), "NEVER_OVERRIDABLE")
    assert refused(cli("approve", "INV-6", "--by", FC, "--override", "PO_AMOUNT_MISMATCH", "--reason", "x"), "NEVER_OVERRIDABLE")   # mixed: one is never
    assert counts(con) == before and item(con, "INV-3")["state"] == "HELD"


def test_override_needs_the_right_flags_and_a_written_reason(world):
    con = db(world); before = counts(con)
    r = cli("approve", "INV-2", "--by", FC)
    assert refused(r, "OVERRIDE_REQUIRED") and "--override PO_AMOUNT_MISMATCH --reason TEXT" in " ".join((r.stderr or "").split())   # tells you exactly what to type
    assert refused(cli("approve", "INV-2", "--by", FC, "--override", "PO_AMOUNT_MISMATCH"), "REASON_REQUIRED")
    assert refused(cli("approve", "INV-2", "--by", FC, "--override", "PO_AMOUNT_MISMATCH", "--reason", "   "), "REASON_REQUIRED")
    assert refused(cli("approve", "INV-2", "--by", FC, "--override", "PO_AMOUNT_MISMATCHH", "--reason", "typo"), "UNKNOWN_OVERRIDE")
    assert refused(cli("approve", "INV-1", "--by", FC, "--override", "PO_AMOUNT_MISMATCH", "--reason", "n/a"), "UNKNOWN_OVERRIDE")    # a flag this item doesn't have
    assert counts(con) == before
    r = cli("approve", "INV-2", "--by", FC, "--override", "PO_AMOUNT_MISMATCH", "--reason", "PO revised by purchasing, ref 7781")
    assert r.exit_code == 0, r.output + (r.stderr or "")
    ap = con.execute("select * from approvals").fetchone()
    assert json.loads(ap["overrides_json"]) == ["PO_AMOUNT_MISMATCH"] and "ref 7781" in ap["reason"] and item(con, "INV-2")["state"] == "APPROVED"
    assert journal.trial_balance(con)["Staff meals"] == 6480000


# ---------------- stale ----------------
def test_changed_client_data_makes_the_approval_stale(world):
    con = db(world); before = counts(con)
    p = world / "clients/northbay/vendor_master.csv"; p.write_text(p.read_text() + "V9,New Vendor,,,,,000000000009,,,,2026-11-01\n")
    r = cli("approve", "INV-1", "--by", FC)
    assert refused(r, "STALE") and "re-run" in " ".join((r.stderr or "").split()).lower()
    assert counts(con) == before and item(con, "INV-1")["state"] == "AWAITING_APPROVAL"


def test_changed_policy_makes_the_approval_stale(world):
    con = db(world); before = counts(con)
    (world / "clients/northbay/policy.yaml").write_text(POLICY + "po:\n  tolerance_pct: 9.0\n")
    assert refused(cli("approve", "INV-1", "--by", FC), "STALE") and counts(con) == before


def test_changed_limits_or_approvers_do_not_stale_a_decision(world):
    cfg = yaml.safe_load((world / "config.yaml").read_text()); cfg["limits"]["max_cost_inr"] = 1.0
    (world / "config.yaml").write_text(yaml.safe_dump(cfg))
    (world / "clients/northbay/policy.yaml").write_text("approvals:\n  approvers:\n    - New Approver\n")
    assert cli("approve", "INV-1", "--by", "New Approver").exit_code == 0               # runtime config is not part of decision_hash


# ---------------- decline -> new approval -> attempt a2 ----------------
def _decline(con, ref):
    """Simulate what A8 will do on a provider-confirmed decline: attempt DECLINED, payment FAILED, item back to HELD."""
    con.execute("update payment_attempts set step='DONE', outcome='DECLINED'")
    con.execute("update payments set status='FAILED'")
    con.execute("update items set state='HELD', outcome='HELD', version=version+1 where source_ref like ?", (f"%#{ref}",))


def test_decline_then_fresh_approval_seq2_then_attempt_a2_linked_to_it(world):
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    con = db(world); it = item(con, "INV-1"); a1 = con.execute("select * from approvals").fetchone()
    pk = con.execute("select payment_key from payments").fetchone()[0]
    _decline(con, "INV-1")
    r = cli("approve", "INV-1", "--by", AP)
    assert r.exit_code == 0 and "retry after a confirmed decline" in " ".join(r.output.split()), r.output + (r.stderr or "")
    aps = con.execute("select * from approvals order by approval_seq").fetchall()
    assert [(a["approval_seq"], a["reviewer"], a["decision_hash"]) for a in aps] == [(1, FC, it["decision_hash"]), (2, AP, it["decision_hash"])]
    assert aps[1]["approval_id"] == ids.approval_id(it["item_id"], it["decision_hash"], 2) != a1["approval_id"]
    pay = con.execute("select * from payments").fetchone()
    assert (pay["payment_key"], pay["status"], pay["current_attempt"], pay["approval_id"]) == (pk, "CREATED", 2, a1["approval_id"])   # key immutable
    atts = con.execute("select * from payment_attempts order by attempt_no").fetchall()
    assert [(a["attempt_key"], a["outcome"], a["approval_id"]) for a in atts] == [(f"{pk}:a1", "DECLINED", aps[0]["approval_id"]),
                                                                                  (f"{pk}:a2", "PENDING", aps[1]["approval_id"])]   # each attempt -> its own approval
    assert approval_ids_not_in_keys(pk, aps)
    assert con.execute("select count(*) from journal_entries where kind='INVOICE_ACCRUAL'").fetchone()[0] == 1       # booked once, not per attempt
    assert item(con, "INV-1")["state"] == "APPROVED"
    n = counts(con); r = cli("approve", "INV-1", "--by", FC)
    assert r.exit_code == 0 and "already approved" in r.output and counts(con) == n                                  # and now a double approve is a no-op again


def approval_ids_not_in_keys(pk, aps):
    return all(a["approval_id"] not in pk for a in aps)


def test_no_retry_unless_the_provider_confirmed_a_decline(world):
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    con = db(world); n = counts(con)
    # an uncertain outcome is NOT a decline: it must be reconciled first, and no second attempt is ever created
    con.execute("update payment_attempts set step='CONFIRM_SENT', outcome='UNKNOWN'"); con.execute("update payments set status='PAYMENT_UNCERTAIN'")
    con.execute("update items set state='HELD', version=version+1 where source_ref like '%#INV-1'")
    r = cli("approve", "INV-1", "--by", AP)
    assert refused(r, "PAYMENT_UNCERTAIN")
    assert counts(con)["approvals"] == n["approvals"] and counts(con)["payments"] == n["payments"]
    # a reversal after confirmed success is never retried
    con.execute("update payment_attempts set step='DONE', outcome='SUCCEEDED'"); con.execute("update payments set status='REVERSED'")
    r = cli("approve", "INV-1", "--by", AP)
    assert refused(r, "NOT_RETRYABLE") and "never retried" in " ".join((r.stderr or "").split())
    assert counts(con)["approvals"] == n["approvals"] and counts(con)["payments"] == n["payments"]


# ---------------- containment ----------------
def test_vendor_containment_is_checked_in_the_enqueue_and_is_recoverable(world):
    con = db(world)
    con.execute("insert into exceptions(exception_id,run_id,item_id,vendor_id,type,severity,status,blocking,summary,details,recommended_action,"
                "evidence_refs_json,related_refs_json,origin_key,created_at,updated_at) select 'xc1', run_id, item_id, 'V2','PAYMENT_RETURNED',"
                "'CRITICAL','CONTAINED',1,'s','d','r','[]','[]','o','t','t' from items limit 1")
    n = counts(con)
    r = cli("approve", "INV-1", "--by", FC)
    assert refused(r, "VENDOR_CONTAINED") and "xc1" in (r.stderr or "")
    c = counts(con)
    assert (c["approvals"], c["payments"], c["payment_attempts"], c["journal_entries"]) == (0, 0, 0, 0)          # nothing was enqueued
    assert item(con, "INV-1")["state"] == "HELD" and c["exceptions"] == n["exceptions"] + 1                       # held + a durable exception
    assert con.execute("select severity, blocking from exceptions where type='VENDOR_CONTAINED'").fetchone()[:] == ("HIGH", 1)
    con.execute("update exceptions set status='RESOLVED' where exception_id='xc1'")                              # resolved with evidence (A11)
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0 and item(con, "INV-1")["state"] == "APPROVED"      # not stuck forever


# ---------------- reject ----------------
def test_reject(world):
    con = db(world)
    assert cli("reject", "INV-2", "--by", AP).exit_code == 2                                                      # --reason is required
    assert refused(cli("reject", "INV-2", "--by", AP, "--reason", "  "), "REASON_REQUIRED")
    assert refused(cli("reject", "INV-2", "--by", "Mallory", "--reason", "x"), "NOT_AN_APPROVER")
    r = cli("reject", "INV-2", "--by", AP, "--reason", "wrong PO")
    assert r.exit_code == 0 and "rejected" in r.output
    ap = con.execute("select * from approvals").fetchone()
    assert (ap["status"], ap["reviewer"], ap["reason"], ap["approval_seq"]) == ("REJECTED", AP, "wrong PO", 1)
    assert item(con, "INV-2")["state"] == "REJECTED" and item(con, "INV-2")["outcome"] == "REJECTED"
    n = counts(con)
    assert cli("reject", "INV-2", "--by", FC, "--reason", "again").exit_code == 0 and counts(con) == n            # double reject: no-op
    assert refused(cli("approve", "INV-2", "--by", FC, "--override", "PO_AMOUNT_MISMATCH", "--reason", "x"), "WRONG_STATE")   # final
    assert cli("reject", "INV-3", "--by", FC, "--reason", "duplicate of an old bill").exit_code == 0               # a never-overridable hold can be rejected
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    assert refused(cli("reject", "INV-1", "--by", FC, "--reason", "changed my mind"), "ALREADY_APPROVED")         # a payment may be in flight
    assert refused(cli("reject", "INV-5", "--by", FC, "--reason", "x"), "WRONG_STATE")                            # CANNOT_VERIFY is final


def test_wrong_kind_or_state_is_refused(world):
    assert refused(cli("approve", "INV-5", "--by", FC), "WRONG_STATE")                                           # CANNOT_VERIFY
    assert cli("run", "--scenario", str(world / "scenarios/BANK")).exit_code == 0
    assert refused(cli("approve", "BL-1", "--by", FC), "NOT_AN_INVOICE")                                         # a bank line is posted, not paid
    assert cli("approve", "BL-999", "--by", FC).exit_code == 2                                                   # unknown item (selector)
    assert refused(cli("approve", "INV-1", "--by", FC, "--run", "R-nope"), "x") is False and cli("approve", "INV-1", "--by", FC, "--run", "R-nope").exit_code == 2


# ---------------- locking, providers, reconcile-first, inline ----------------
def test_approve_takes_the_lock(world):
    with RunLock(world / "var" / "paypilot.lock"):
        assert cli("approve", "INV-1", "--by", FC).exit_code == 3
    assert counts(db(world))["approvals"] == 0


def test_provider_is_checked_before_anything_is_written(world, monkeypatch):
    n = counts(db(world))
    assert cli("approve", "INV-1", "--by", FC, "--provider", "razorpay_live").exit_code == 2
    for k in ("RAZORPAYX_KEY_ID", "RAZORPAYX_KEY_SECRET", "RAZORPAYX_ACCOUNT_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    assert cli("approve", "INV-1", "--by", FC, "--provider", "razorpayx_test").exit_code == 2
    assert counts(db(world)) == n


def test_reconcile_comes_first_an_in_flight_attempt_blocks_new_approvals(world):
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    con = db(world)
    con.execute("update payment_attempts set step='CONFIRM_SENT', outcome='PENDING'")                          # sent; outcome unknown
    before = counts(con)
    r = cli("approve", "INV-4", "--by", FC)
    assert refused(r, "PAYMENT_UNCERTAIN")
    after = counts(con)
    assert after["approvals"] == before["approvals"] and after["payments"] == before["payments"]
    assert item(con, "INV-4")["state"] == "AWAITING_APPROVAL"


def test_inline_execution_is_a8_the_approval_is_durable_even_so(world):
    cfg = yaml.safe_load((world / "config.yaml").read_text()); cfg["payments"]["approve_executes_inline"] = True
    (world / "config.yaml").write_text(yaml.safe_dump(cfg))
    r = cli("approve", "INV-1", "--by", FC)
    assert r.exit_code == 0, (r.output, r.stderr)
    con = db(world)
    assert con.execute("select count(*) from approvals").fetchone()[0] == 1
    assert con.execute("select count(*) from payments").fetchone()[0] == 1


# ---------------- crash safety ----------------
def test_kill_right_after_the_enqueue_commit_leaves_exactly_one_of_everything(world):
    env = {**os.environ, "PAYPILOT_ROOT": str(world), "BANK_HMAC_KEY": "test-key-0123456789", "PYTHONPATH": str(ROOT), "PAYPILOT_CRASH_AT": "after_payment_insert"}
    r = subprocess.run([sys.executable, "-m", "paypilot", "approve", "INV-1", "--by", FC], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 137, r.stderr
    con = db(world)
    assert counts(con)["approvals"] == 1 and counts(con)["payments"] == 1 and counts(con)["payment_attempts"] == 1
    assert con.execute("select count(*) from journal_entries").fetchone()[0] == 1 and item(con, "INV-1")["state"] == "APPROVED"
    n = counts(con)
    env.pop("PAYPILOT_CRASH_AT")
    r = subprocess.run([sys.executable, "-m", "paypilot", "approve", "INV-1", "--by", FC], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "already approved" in " ".join(r.stdout.split())
    assert {k: v for k, v in counts(con).items() if k != "audit_events"} == {k: v for k, v in n.items() if k != "audit_events"}      # no second payment


# ---------------- the contract entry point ----------------
def test_contract_entry_point_approve_returns_the_approval(world):
    from paypilot.approvals.service import ApprovalRefused, approve
    con = db(world); it = item(con, "INV-1")
    a = approve(it["item_id"], FC, None, [])
    assert (a.status, a.reviewer, a.approval_seq, a.decision_hash) == ("APPROVED", FC, 1, it["decision_hash"])
    assert approve(it["item_id"], AP, None, []).approval_id == a.approval_id                                      # no-op returns the original
    with pytest.raises(ApprovalRefused) as e:
        approve(item(con, "INV-3")["item_id"], FC, "x", ["DUPLICATE_INVOICE"])
    assert e.value.code == "NEVER_OVERRIDABLE"


def test_contract_entry_point_also_reconciles_first_and_validates_the_provider(world, monkeypatch):
    """Review finding: approvals.service.approve() used to skip the reconcile-first gate and the provider check the CLI did."""
    from paypilot.approvals.service import ApprovalRefused, approve
    from paypilot.cli.runtime import CliError
    assert cli("approve", "INV-1", "--by", FC).exit_code == 0
    con = db(world)
    con.execute("update payment_attempts set step='CONFIRM_SENT', outcome='PENDING'")                       # sent; outcome unknown
    n = counts(con)
    with pytest.raises(ApprovalRefused, match="reconcile it before approving"):
        approve(item(con, "INV-4")["item_id"], FC, None, [])                                              # not just the CLI: every entry point
    assert counts(con)["approvals"] == n["approvals"] and counts(con)["payments"] == n["payments"]
    assert item(con, "INV-4")["state"] == "AWAITING_APPROVAL"
    con.execute("update payment_attempts set step='NONE', outcome='PENDING'")
    cfg = yaml.safe_load((world / "config.yaml").read_text()); cfg["payments"]["provider"] = "razorpayx_test"
    (world / "config.yaml").write_text(yaml.safe_dump(cfg))
    for k in ("RAZORPAYX_KEY_ID", "RAZORPAYX_KEY_SECRET", "RAZORPAYX_ACCOUNT_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(CliError):
        approve(item(con, "INV-4")["item_id"], FC, None, [])                                              # test keys required, never silently fake
    assert counts(con)["approvals"] == n["approvals"] and counts(con)["payments"] == n["payments"]
