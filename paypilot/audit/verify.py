"""audit.verify (A17): replay a run from the database and report every discrepancy. Owner: lane A.

Never fixes anything: a discrepancy is reported with the entity named, and `audit verify` exits non-zero.
Checks: decision replay (hash re-derived from stored facts + the run's policy snapshot; decide() re-run on the stored resolution and
checks), approval <-> payment <-> attempt <-> journal links, at most one confirmed attempt per payment key, balanced entries and a
trial balance of zero, unbroken per-entity audit chains, every evidence row resolves to a stored source.
"""
from __future__ import annotations

import json
import sqlite3

from paypilot.core import ids, journal
from paypilot.core.config import decision_hash_input
from paypilot.core.db import connect
from paypilot.policy_config import PolicyConfig
from paypilot.schemas import (AuditVerifyReport, CheckResult, Decision, Invoice, BankLine, VendorMessage, ParseFailure, Resolution, RunConfig)

_MODELS = {"BankLine": BankLine, "Invoice": Invoice, "VendorMessage": VendorMessage, "ParseFailure": ParseFailure}


def _d(kind: str, entity: str, detail: str) -> dict:
    return {"kind": kind, "entity": entity, "detail": detail}


def _decisions(con: sqlite3.Connection, run: sqlite3.Row, out: list[dict]) -> int:
    snap = json.loads(run["policy_snapshot_json"])
    from paypilot.core.config import POLICY_SECTIONS, OPERATIONAL_SECTIONS
    try:
        policy = PolicyConfig(**{k: snap[k] for k in (*OPERATIONAL_SECTIONS, *POLICY_SECTIONS)})
        policy = policy.model_copy(update={"decision_hash_input": decision_hash_input(run["policy_hash"], run["data_hash"])})
    except Exception as e:                                             # a snapshot that cannot be loaded is itself a discrepancy
        out.append(_d("POLICY_SNAPSHOT", run["run_id"], f"cannot load the stored policy snapshot: {e}"))
        return 0
    n = 0
    for r in con.execute("SELECT * FROM items WHERE run_id=? AND decision_json IS NOT NULL ORDER BY item_id", (run["run_id"],)):
        d = Decision.model_validate_json(r["decision_json"])
        n += 1
        if d.decision_hash != r["decision_hash"]:
            out.append(_d("DECISION_HASH", r["item_id"], "stored decision_hash differs from the decision's own hash"))
        if ids.decision_hash(d.facts, policy.decision_hash_input) != r["decision_hash"]:
            out.append(_d("DECISION_HASH", r["item_id"], "decision_hash does not re-derive from the stored facts + policy snapshot"))
            continue
        if r["kind"] == "ParseFailure" or not r["resolution_json"] or not r["checks_json"]:
            continue
        try:
            from paypilot.decide.engine import decide
            item = _MODELS[r["kind"]].model_validate_json(r["payload_json"])
            res = Resolution.model_validate_json(r["resolution_json"])
            checks = [CheckResult.model_validate(c) for c in json.loads(r["checks_json"])]
            again = decide(item, res, checks, policy)
        except NotImplementedError:
            continue
        except Exception as e:
            out.append(_d("DECISION_REPLAY", r["item_id"], f"decide() could not be re-run: {e}"))
            continue
        if (again.proposal, sorted(again.flags), again.decision_hash) != (d.proposal, sorted(d.flags), d.decision_hash):
            out.append(_d("DECISION_REPLAY", r["item_id"],
                          f"replay gives {again.proposal.value} {sorted(again.flags)} but {d.proposal.value} {sorted(d.flags)} is stored"))
    return n


def _payments(con: sqlite3.Connection, run_id: str, out: list[dict]) -> None:
    for p in con.execute("SELECT p.*, i.state AS item_state, i.run_id FROM payments p JOIN items i ON i.item_id=p.item_id WHERE i.run_id=?", (run_id,)):
        pid = p["payment_id"]
        atts = con.execute("SELECT * FROM payment_attempts WHERE payment_id=? ORDER BY attempt_no", (pid,)).fetchall()
        if not atts:
            out.append(_d("ORPHAN_PAYMENT", pid, "payment has no attempt row"))
        if sum(1 for a in atts if a["outcome"] == "SUCCEEDED") > 1:
            out.append(_d("DUPLICATE_SUCCESS", pid, "more than one confirmed attempt for one payment key"))
        for a in atts:
            ap = con.execute("SELECT * FROM approvals WHERE approval_id=?", (a["approval_id"],)).fetchone()
            if ap is None or ap["status"] != "APPROVED" or ap["item_id"] != p["item_id"]:
                out.append(_d("APPROVAL_LINK", a["attempt_key"], "attempt is not linked to an APPROVED approval of the same item"))
            if a["attempt_key"] != ids.attempt_key(p["payment_key"], a["attempt_no"]):
                out.append(_d("ATTEMPT_KEY", a["attempt_key"], "attempt_key does not derive from the immutable payment_key"))
            if a["step"] != "NONE" and a["outcome"] == "PENDING" and a["provider_ref"] is None and p["status"] not in ("SUBMITTED", "PAYMENT_UNCERTAIN"):
                out.append(_d("ATTEMPT_STATE", a["attempt_key"], "attempt was sent but has no provider_ref and the payment is not uncertain"))
        jp = con.execute("SELECT * FROM journal_entries WHERE item_id=? AND kind='INVOICE_PAYMENT'", (p["item_id"],)).fetchone()
        rv = con.execute("SELECT 1 FROM journal_entries WHERE item_id=? AND kind='REVERSAL'", (p["item_id"],)).fetchone()
        confirmed = p["status"] in ("PROVIDER_CONFIRMED", "RETURNED", "REVERSED")
        if confirmed and jp is None:
            out.append(_d("JOURNAL_LINK", pid, "payment was confirmed but has no INVOICE_PAYMENT journal entry"))
        if jp is not None and not confirmed:
            out.append(_d("JOURNAL_LINK", pid, "INVOICE_PAYMENT journal entry exists for a payment that was never confirmed"))
        if jp is not None and not any(a["attempt_key"] == jp["attempt_key"] and a["outcome"] == "SUCCEEDED" for a in atts):
            out.append(_d("JOURNAL_LINK", pid, "INVOICE_PAYMENT entry is not linked to a confirmed attempt"))
        if p["status"] in ("RETURNED", "REVERSED") and rv is None:
            out.append(_d("JOURNAL_LINK", pid, "payment was returned but has no REVERSAL entry"))
        if p["item_state"] == "PAID" and p["status"] != "PROVIDER_CONFIRMED":
            out.append(_d("ITEM_PAYMENT", p["item_id"], f"item is PAID but its payment is {p['status']}"))
        if p["status"] == "PROVIDER_CONFIRMED" and p["item_state"] != "PAID":
            out.append(_d("ITEM_PAYMENT", p["item_id"], f"payment is PROVIDER_CONFIRMED but the item is {p['item_state']}"))
    for i in con.execute("SELECT item_id FROM items WHERE run_id=? AND state='PAID'", (run_id,)):
        if con.execute("SELECT 1 FROM payments WHERE item_id=?", (i["item_id"],)).fetchone() is None:
            out.append(_d("ORPHAN_PAID", i["item_id"], "item is PAID but has no payment"))


def _journal(con: sqlite3.Connection, run_id: str, out: list[dict]) -> dict[str, int]:
    for e in con.execute("SELECT e.entry_id, e.kind, e.item_id, SUM(l.debit_paise) AS dr, SUM(l.credit_paise) AS cr FROM journal_entries e "
                         "JOIN journal_lines l ON l.entry_id=e.entry_id JOIN items i ON i.item_id=e.item_id WHERE i.run_id=? GROUP BY e.entry_id", (run_id,)):
        if e["dr"] != e["cr"]:
            out.append(_d("UNBALANCED_ENTRY", e["entry_id"], f"{e['kind']} for {e['item_id']}: Dr {e['dr']} != Cr {e['cr']}"))
    tb = journal.trial_balance(con, run_id)
    if sum(tb.values()) != 0:
        out.append(_d("TRIAL_BALANCE", run_id, f"trial balance does not sum to zero ({sum(tb.values())})"))
    return tb


def _chains(con: sqlite3.Connection, run_id: str, out: list[dict]) -> None:
    last: dict[tuple[str, str], str | None] = {}
    first: set[tuple[str, str]] = set()
    for e in con.execute("SELECT * FROM audit_events WHERE run_id=? AND entity_type IN ('item','payment') ORDER BY event_id", (run_id,)):
        k = (e["entity_type"], e["entity_id"])
        if k in last:
            if e["previous_state"] != last[k]:
                out.append(_d("AUDIT_CHAIN", e["entity_id"], f"event {e['event_id']}: previous_state {e['previous_state']} but the chain was at {last[k]}"))
        elif e["entity_type"] == "item" and e["previous_state"] not in ("NEW", None):
            out.append(_d("AUDIT_CHAIN", e["entity_id"], f"event {e['event_id']}: the first recorded transition starts at {e['previous_state']}"))
        last[k] = e["new_state"]
        first.add(k)
    for (etype, eid), state in last.items():
        row = con.execute(f"SELECT {'state' if etype == 'item' else 'status'} AS s FROM {'items' if etype == 'item' else 'payments'} WHERE "
                          f"{'item_id' if etype == 'item' else 'payment_id'}=?", (eid,)).fetchone()
        if row is None:
            out.append(_d("AUDIT_CHAIN", eid, f"audit events refer to a {etype} that does not exist"))
        elif row["s"] != state:
            out.append(_d("AUDIT_CHAIN", eid, f"{etype} is {row['s']} but its last audit event says {state}"))
    for i in con.execute("SELECT item_id, state FROM items WHERE run_id=? AND state != 'NEW'", (run_id,)):
        if ("item", i["item_id"]) not in first:
            out.append(_d("AUDIT_CHAIN", i["item_id"], f"item is {i['state']} but has no audit events"))


def _sources(con: sqlite3.Connection, run_id: str, out: list[dict]) -> None:
    for r in con.execute("SELECT e.item_id, e.source_id FROM item_evidence e JOIN items i ON i.item_id=e.item_id WHERE i.run_id=? "
                         "AND NOT EXISTS (SELECT 1 FROM sources s WHERE s.source_id=e.source_id)", (run_id,)):
        out.append(_d("SOURCE_MISSING", r["item_id"], f"evidence refers to source {r['source_id']} which is not stored"))


def verify_run(run_id: str, cfg: RunConfig) -> AuditVerifyReport:
    """Replay decisions from stored facts + policy snapshot (INTERFACES §9)."""
    con = connect(cfg.storage["db_path"], apply_schema=False)
    try:
        run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ValueError(f"unknown run {run_id!r}")
        out: list[dict] = []
        replayed = _decisions(con, run, out)
        _payments(con, run_id, out)
        tb = _journal(con, run_id, out)
        _chains(con, run_id, out)
        _sources(con, run_id, out)
        n = con.execute("SELECT COUNT(*) FROM items WHERE run_id=?", (run_id,)).fetchone()[0]
        return AuditVerifyReport(run_id=run_id, ok=not out, items_checked=n, decisions_replayed=replayed, discrepancies=out, trial_balance=tb)
    finally:
        con.close()
