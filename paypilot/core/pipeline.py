"""core.pipeline (A4): ingest -> resolve -> check -> decide -> classify -> post / queue / hold. Owner: lane A.

State-driven and resumable: `drive` advances every item from whatever state is persisted, one transition at a time, each with
its audit event in the same transaction. Logic stages (resolve/check/decide/classify) are pure, so re-running one after a crash
is harmless. The financial side effect (journal entry) is in the SAME transaction as the POSTED transition.
Payment execution is NOT here: items stop at AWAITING_APPROVAL (A7/A8 take over).
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from paypilot.checks.runner import run_checks
from paypilot.context import PaymentProvider, StageContext
from paypilot.core import ids, journal
from paypilot.core.audit import append_event
from paypilot.core.crash import crash_point
from paypilot.core.db import connect, tx
from paypilot.core.history import SqliteHistory
from paypilot.core.state import transition
from paypilot.decide.engine import decide
from paypilot.exceptions.classify import classify
from paypilot.ledger.budget import effective_cost_limit
from paypilot.ledger.store import summarize
from paypilot.resolve.matcher import resolve_vendor
from paypilot.schemas import (BankLine, CheckResult, Decision, ExceptionDraft, Facts, Invoice, ItemState as S,
                              ParseFailure, Proposal, Reason, Request, Resolution, RunConfig, RunStatus,
                              RunSummary, VendorMessage)

ACTOR = "system"
_MODELS = {"BankLine": BankLine, "Invoice": Invoice, "VendorMessage": VendorMessage, "ParseFailure": ParseFailure}
_PIPELINE_STATES = {S.NEW, S.PARSED, S.RESOLVED, S.CHECKED, S.DECIDED}
_PROPOSAL_STATE = {Proposal.AWAITING_APPROVAL: S.AWAITING_APPROVAL, Proposal.HELD: S.HELD,
                   Proposal.CANNOT_VERIFY: S.CANNOT_VERIFY}


# ---------------- sessions ----------------
def open_session(con: sqlite3.Connection, run_id: str, command: str, now: datetime) -> str:
    sid = uuid.uuid4().hex[:12]
    with tx(con):
        con.execute("INSERT INTO sessions(session_id,run_id,command,pid,started_at) VALUES (?,?,?,?,?)",
                    (sid, run_id, command, os.getpid(), now.isoformat()))
    return sid


def close_session(con: sqlite3.Connection, session_id: str, exit_code: int, now: datetime) -> None:
    with tx(con):
        con.execute("UPDATE sessions SET ended_at=?, exit_code=? WHERE session_id=?", (now.isoformat(), exit_code, session_id))


# ---------------- ingest ----------------
def _item_columns(item: Any) -> dict[str, Any]:
    if isinstance(item, BankLine):
        return dict(item_date=item.date.isoformat(), amount_paise=item.amount_paise, currency=item.currency,
                    descriptor_key=item.descriptor_key, transaction_fingerprint=item.transaction_fingerprint)
    if isinstance(item, Invoice):
        return dict(item_date=item.invoice_date.isoformat() if item.invoice_date else None, amount_paise=item.amount_paise,
                    currency=item.currency, invoice_no_canonical=item.invoice_no_canonical,
                    transaction_fingerprint=item.transaction_fingerprint)
    if isinstance(item, VendorMessage):
        return dict(item_date=item.received_at.date().isoformat())
    return {}


def ingest_run(con: sqlite3.Connection, *, request: Request, cfg: RunConfig, snapshot: dict, data_hash: str,
               items: list[BankLine | Invoice | VendorMessage | ParseFailure], now: datetime) -> tuple[str, bool]:
    """Persist the run + one row per input row (deterministic ids, INSERT OR IGNORE). Returns (run_id, created).
    The same request file is the same run: a second ingest adds nothing."""
    run_id = ids.run_id(request.client_id, request.source_sha)
    with tx(con):
        cur = con.execute(
            "INSERT OR IGNORE INTO runs(run_id,client_id,request_id,request_sha,operation,config_hash,policy_hash,data_hash,"
            "policy_snapshot_json,status,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, request.client_id, request.request_id, request.source_sha,
             request.operation.value if request.operation else None, cfg.config_hash, cfg.policy_hash, data_hash,
             ids.canonical_json(snapshot), RunStatus.RUNNING.value, now.isoformat(), now.isoformat()))
        created = cur.rowcount == 1
        counters: dict[str, int] = {}
        for item in items:
            input_name = item.source_ref.split("#", 1)[0]
            counters[input_name] = row_no = counters.get(input_name, 0) + 1
            cols = _item_columns(item)
            con.execute(
                "INSERT OR IGNORE INTO items(item_id,client_id,run_id,kind,input_name,row_no,source_ref,operation,state,version,"
                "payload_json,parse_errors_json,transaction_fingerprint,invoice_no_canonical,descriptor_key,amount_paise,currency,"
                "item_date,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item.item_id, request.client_id, run_id, type(item).__name__, input_name, row_no, item.source_ref,
                 request.operation.value if request.operation else "", S.NEW.value, 0, item.model_dump_json(),
                 json.dumps(item.errors if isinstance(item, ParseFailure) else []), cols.get("transaction_fingerprint"),
                 cols.get("invoice_no_canonical"), cols.get("descriptor_key"), cols.get("amount_paise"), cols.get("currency"),
                 cols.get("item_date"), now.isoformat()))
    crash_point("after_ingest")
    return run_id, created


# ---------------- evidence + exceptions persistence ----------------
def _persist_evidence(con: sqlite3.Connection, item_id: str, decision_hash: str, resolution: Resolution,
                      checks: list[CheckResult]) -> None:
    pairs = [("", s) for s in resolution.evidence] + [(c.check_id, s) for c in checks for s in c.evidence]
    for check_id, s in pairs:
        con.execute("INSERT OR IGNORE INTO sources(source_id,provider,record_ref,fact,value,field_or_passage,record_date,"
                    "record_date_basis,retrieved_at,limitations_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (s.source_id, s.provider, s.record_ref, s.fact, s.value, s.field_or_passage,
                     s.record_date.isoformat() if s.record_date else None, s.record_date_basis.value,
                     s.retrieved_at.isoformat(), json.dumps(s.limitations)))
        con.execute("INSERT OR IGNORE INTO item_evidence(item_id,decision_hash,source_id,check_id) VALUES (?,?,?,?)",
                    (item_id, decision_hash, s.source_id, check_id))


def upsert_exceptions(con: sqlite3.Connection, *, run_id: str, session_id: str, item_id: str, vendor_id: str | None,
                      drafts: list[ExceptionDraft], now: datetime) -> list[str]:
    """Insert drafts once (exception_id is deterministic). Inside the caller's txn. A11 adds lifecycle on top of this."""
    created: list[str] = []
    for d in drafts:
        exc_item = d.item_id or item_id
        eid = ids.exception_id(exc_item, d.payment_id, d.type, d.origin_key)
        status = "CONTAINED" if d.blocking else "CLASSIFIED"
        cur = con.execute(
            "INSERT OR IGNORE INTO exceptions(exception_id,run_id,item_id,payment_id,vendor_id,type,severity,status,blocking,summary,"
            "details,recommended_action,evidence_refs_json,related_refs_json,origin_key,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, run_id, exc_item, d.payment_id, vendor_id, d.type, d.severity.value, status, int(d.blocking), d.summary,
             d.details, d.recommended_action, json.dumps(d.evidence_refs), json.dumps(d.related_refs), d.origin_key,
             now.isoformat(), now.isoformat()))
        if cur.rowcount == 1:
            created.append(eid)
            append_event(con, run_id=run_id, session_id=session_id, entity_type="exception", entity_id=eid, previous_state=None,
                         new_state=status, actor=ACTOR, ts=now, reason=d.summary,
                         metadata={"type": d.type, "severity": d.severity.value, "item_id": exc_item})
    return created


# ---------------- stages ----------------
def _parse_failure_decision(item: ParseFailure, ctx: StageContext) -> Decision:
    rule = next((r for r in ctx.policy.decision.rules if "PARSE_FAILED" in {f.value for f in r.flags_any}), None)
    facts = Facts(operation=ctx.operation, kind="ParseFailure", flags=["PARSE_FAILED"])
    return Decision(item_id=item.item_id, proposal=Proposal.CANNOT_VERIFY, flags=["PARSE_FAILED"],
                    reasons=[Reason(code="PARSE_FAILED", text="Required input missing or unreadable: " + "; ".join(item.errors),
                                    rule_id=rule.id if rule else None)],
                    missing=sorted({e.split(":", 1)[0] for e in item.errors}), rule_ids=[rule.id] if rule else [],
                    facts=facts, decision_hash=ids.decision_hash(facts, ctx.policy.decision_hash_input))


def _audit_meta(d: Decision) -> dict[str, Any]:
    return {"decision_hash": d.decision_hash, "proposal": d.proposal.value, "flags": d.flags, "rule_ids": d.rule_ids}


def _advance_one(con: sqlite3.Connection, ctx: StageContext, item_id: str) -> bool:
    """One stage for one item. Returns False when the item is not in a pipeline state any more."""
    row = con.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
    state, ver = S(row["state"]), row["version"]
    if state not in _PIPELINE_STATES:
        return False
    item = _MODELS[row["kind"]].model_validate_json(row["payload_json"])
    now = ctx.clock()
    common = dict(item_id=item_id, run_id=ctx.run_id, session_id=ctx.session_id, actor=ACTOR, ts=now)

    if state is S.NEW:
        if isinstance(item, ParseFailure):
            d = _parse_failure_decision(item, ctx)
            drafts = classify(None, d, None, None, ctx.policy)
            with tx(con):
                transition(con, expected_state=S.NEW, expected_version=ver, new_state=S.CANNOT_VERIFY,
                           reason="; ".join(item.errors), metadata=_audit_meta(d),
                           set_columns={"decision_json": d.model_dump_json(), "decision_hash": d.decision_hash}, **common)
                upsert_exceptions(con, run_id=ctx.run_id, session_id=ctx.session_id, item_id=item_id, vendor_id=None,
                                  drafts=drafts, now=now)
        else:
            transition(con, expected_state=S.NEW, expected_version=ver, new_state=S.PARSED, **common)
    elif state is S.PARSED:
        res = resolve_vendor(item, ctx)
        transition(con, expected_state=S.PARSED, expected_version=ver, new_state=S.RESOLVED,
                   metadata={"resolution": res.status}, set_columns={"resolution_json": res.model_dump_json(),
                                                                     "vendor_id": res.vendor_id}, **common)
        crash_point("after_resolve")
    elif state is S.RESOLVED:
        res = Resolution.model_validate_json(row["resolution_json"])
        checks = run_checks(item, res, ctx)
        transition(con, expected_state=S.RESOLVED, expected_version=ver, new_state=S.CHECKED,
                   metadata={"checks": {c.check_id: c.status.value for c in checks}},
                   set_columns={"checks_json": json.dumps([c.model_dump(mode="json") for c in checks])}, **common)
        crash_point("after_check")
    elif state is S.CHECKED:
        res = Resolution.model_validate_json(row["resolution_json"])
        checks = [CheckResult.model_validate(c) for c in json.loads(row["checks_json"])]
        d = decide(item, res, checks, ctx.policy)
        with tx(con):
            _persist_evidence(con, item_id, d.decision_hash, res, checks)
            transition(con, expected_state=S.CHECKED, expected_version=ver, new_state=S.DECIDED, metadata=_audit_meta(d),
                       set_columns={"decision_json": d.model_dump_json(), "decision_hash": d.decision_hash}, **common)
        crash_point("after_decide")
    else:  # DECIDED -> post / queue / hold
        d = Decision.model_validate_json(row["decision_json"])
        drafts = classify(item, d, None, None, ctx.policy)
        vendor_id = row["vendor_id"]
        if d.proposal is Proposal.RECONCILED:
            raise NotImplementedError("RECONCILED items are produced by the RECONCILE operation (task A12)")
        if d.proposal is Proposal.POSTED:
            if not isinstance(item, BankLine):
                raise ValueError(f"proposal POSTED is only valid for bank lines, got {type(item).__name__}")
            entry = journal.bank_line_posting(item, d, now)
            with tx(con):                                     # journal + state change + audit + exceptions: ONE transaction
                journal.post_entry(con, entry)
                transition(con, expected_state=S.DECIDED, expected_version=ver, new_state=S.POSTED,
                           metadata=_audit_meta(d), **common)
                upsert_exceptions(con, run_id=ctx.run_id, session_id=ctx.session_id, item_id=item_id, vendor_id=vendor_id,
                                  drafts=drafts, now=now)
            crash_point("after_post_txn")
        else:
            with tx(con):
                transition(con, expected_state=S.DECIDED, expected_version=ver, new_state=_PROPOSAL_STATE[d.proposal],
                           reason="; ".join(r.text for r in d.reasons) or None, metadata=_audit_meta(d), **common)
                upsert_exceptions(con, run_id=ctx.run_id, session_id=ctx.session_id, item_id=item_id, vendor_id=vendor_id,
                                  drafts=drafts, now=now)
    return True


# ---------------- summary ----------------
def build_summary(con: sqlite3.Connection, run_id: str, ctx: StageContext) -> RunSummary:
    run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    counts: dict[str, int] = {}
    for r in con.execute("SELECT COALESCE(outcome, state) AS k, COUNT(*) AS n FROM items WHERE run_id=? GROUP BY k", (run_id,)):
        counts[r["k"]] = r["n"]
    open_exc = {r["severity"]: r["n"] for r in con.execute(
        "SELECT severity, COUNT(*) AS n FROM exceptions WHERE run_id=? AND status NOT IN "
        "('RESOLVED','FALSE_POSITIVE','ACCEPTED_RISK','CLOSED_NO_ACTION') GROUP BY severity", (run_id,))}
    sessions = con.execute("SELECT COUNT(*) FROM sessions WHERE run_id=?", (run_id,)).fetchone()[0]
    crashed = con.execute("SELECT COUNT(*) FROM sessions WHERE run_id=? AND ended_at IS NULL AND session_id != ?",
                          (run_id, ctx.session_id)).fetchone()[0] > 0
    cost = summarize(con, run_id, limit_inr=effective_cost_limit(con, run_id, ctx.cfg.limits),
                     unknown_reserve_inr=float(ctx.cfg.costs.get("unknown_reserve_inr", 1.0)))
    elapsed = int((ctx.clock() - datetime.fromisoformat(run["started_at"])).total_seconds() * 1000)
    return RunSummary(run_id=run_id, request_id=run["request_id"], client_id=run["client_id"], operation=ctx.operation,
                      status=RunStatus(run["status"]), counts=counts, open_exceptions=open_exc, sessions=sessions,
                      recovered_from_crash=crashed, elapsed_ms=max(elapsed, 0), cost=cost)


# ---------------- drive ----------------
def drive(run_id: str, ctx: StageContext, provider: PaymentProvider) -> RunSummary:
    """Advance every item of the run as far as the pipeline goes (to POSTED / AWAITING_APPROVAL / HELD / CANNOT_VERIFY).
    `provider` is unused here: payments start at approval (A7/A8)."""
    con = connect(ctx.cfg.storage["db_path"])
    ctx = dataclasses.replace(ctx, history=SqliteHistory(con, ctx.data, ctx.client_id, run_id))
    if ctx.operation is not None:     # missing/unsupported operation: run-level CANNOT_VERIFY result, built in A13
        for r in con.execute("SELECT item_id FROM items WHERE run_id=? ORDER BY COALESCE(item_date,''), input_name, row_no",
                             (run_id,)).fetchall():
            while _advance_one(con, ctx, r["item_id"]):
                pass
    with tx(con):
        con.execute("UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
                    (RunStatus.COMPLETE.value, ctx.clock().isoformat(), run_id))
    return build_summary(con, run_id, ctx)
