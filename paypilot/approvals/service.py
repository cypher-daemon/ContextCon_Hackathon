"""approvals.service (A7): approve / reject, bound to the decision_hash. Owner: lane A.

Rules enforced here (INTERFACES §5/§10, FAILURE_MODES #30-#33, #6):
- the reviewer must be in the client's allowlist (empty list = any declared name); names are validated, never trusted;
- an approval binds the item's CURRENT decision_hash; if data or policy changed since the decision, it is refused as stale;
- NEVER_OVERRIDABLE flags can never be approved, with or without --override; every flag on a HELD item must be explicitly
  overridden, each override must be overridable for this decision, and an override needs a written reason;
- a second approve is a no-op ("already approved by X"); `approval_seq` n+1 exists ONLY after a provider-confirmed decline,
  and attempt n+1 is then linked to that new approval; a reversal after success is never retried;
- vendor containment is checked INSIDE the enqueue transaction;
- the enqueue is ONE transaction: approval + payment (CREATED) + attempt + accrual journal entry + item APPROVED + audit events.
  Nothing here calls a provider: execution is payments.executor (A8).
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from paypilot.core import ids, journal
from paypilot.core.audit import append_event
from paypilot.core.crash import crash_point
from paypilot.core.db import tx
from paypilot.core.pipeline import upsert_exceptions
from paypilot.core.state import transition
from paypilot.flags import NEVER_OVERRIDABLE
from paypilot.schemas import Approval, Decision, ExceptionDraft, Invoice, ItemState as S, RunConfig, Severity

NEVER = frozenset(f.value for f in NEVER_OVERRIDABLE)
APPROVED_STATES = frozenset({S.APPROVED, S.PAYMENT_IN_PROGRESS, S.PAID, S.PAYMENT_UNCERTAIN})
_NAME = re.compile(r"^[^\x00-\x1f\x7f-\x9f  ]{1,100}$")
ACTOR_SYSTEM = "system"


class ApprovalRefused(Exception):
    """A refused approve/reject. `code` is machine-readable (tests and the CLI); the message tells the human what to do."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class ApprovalResult:
    outcome: str                     # APPROVED | ALREADY_APPROVED | REJECTED | ALREADY_REJECTED
    approval: Approval
    payment_id: str | None = None
    attempt_key: str | None = None
    attempt_no: int | None = None
    retry: bool = False


def check_reviewer(reviewer: str | None, allowlist: list[str] | None) -> str:
    name = (reviewer or "").strip()
    if not _NAME.match(name) or any(unicodedata.category(c) in ("Cc", "Cf") for c in name):
        raise ApprovalRefused("INVALID_REVIEWER", "--by must be 1-100 printable characters (no control or invisible characters)")
    if allowlist and name not in allowlist:
        raise ApprovalRefused("NOT_AN_APPROVER", f"{name!r} is not in this client's approver allowlist")
    return name


def _approval(r: sqlite3.Row) -> Approval:
    return Approval(approval_id=r["approval_id"], item_id=r["item_id"], decision_hash=r["decision_hash"], approval_seq=r["approval_seq"],
                    status=r["status"], reviewer=r["reviewer"], reason=r["reason"], overrides=json.loads(r["overrides_json"]),
                    requested_at=r["requested_at"], decided_at=r["decided_at"])


def _check_overrides(decision: Decision, state: S, overrides: list[str], reason: str | None) -> None:
    flags = set(decision.flags)
    never = sorted(f for f in flags if f in NEVER)
    if never:
        raise ApprovalRefused("NEVER_OVERRIDABLE", f"{', '.join(never)} can never be overridden or approved; "
                                                    "fix the cause (vendor master, evidence) and re-run")
    unknown = sorted(set(overrides) - flags)
    if unknown:
        raise ApprovalRefused("UNKNOWN_OVERRIDE", f"--override {', '.join(unknown)}: not a flag on this decision ({sorted(flags) or 'none'})")
    not_ok = sorted(o for o in overrides if o in NEVER or o not in set(decision.overridable))
    if not_ok:
        raise ApprovalRefused("NOT_OVERRIDABLE", f"{', '.join(not_ok)} is not overridable under this client's policy")
    if state is S.HELD:
        missing = sorted(flags - set(overrides))
        if missing:
            raise ApprovalRefused("OVERRIDE_REQUIRED", f"this item is held for {', '.join(missing)}; "
                                                         f"approve with " + " ".join(f"--override {m}" for m in missing) + " --reason TEXT")
    if overrides and not reason:
        raise ApprovalRefused("REASON_REQUIRED", "an override needs a written --reason")


def _state_message(state: S) -> str:
    return (f"item is {state.value}: only items AWAITING_APPROVAL, or HELD with overridable flags, can be approved"
            if state is not S.REJECTED else "item was REJECTED; a rejected item is final")


def approve_item(con: sqlite3.Connection, *, item_id: str, reviewer: str, reason: str | None, overrides: list[str], cfg: RunConfig,
                 policy: Any, history: Any, provider_name: str, session_id: str, now: datetime) -> ApprovalResult:
    reviewer = check_reviewer(reviewer, cfg.approvals.get("approvers"))
    reason = (reason or "").strip() or None
    overrides = sorted({o.strip() for o in overrides if o and o.strip()})
    contained: list[str] | None = None
    result: ApprovalResult | None = None
    with tx(con):
        row = con.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ApprovalRefused("UNKNOWN_ITEM", f"no item {item_id}")
        state = S(row["state"])
        # --- double approve = no-op ---
        if state in APPROVED_STATES:
            prev = con.execute("SELECT * FROM approvals WHERE item_id=? AND status='APPROVED' ORDER BY approval_seq DESC, rowid DESC LIMIT 1",
                               (item_id,)).fetchone()
            if prev is None:
                raise ApprovalRefused("WRONG_STATE", _state_message(state))
            pay = con.execute("SELECT * FROM payments WHERE item_id=?", (item_id,)).fetchone()
            return ApprovalResult("ALREADY_APPROVED", _approval(prev), pay["payment_id"] if pay else None)
        if row["kind"] != "Invoice":
            raise ApprovalRefused("NOT_AN_INVOICE", f"only invoices can be paid; this is a {row['kind']}")
        if state not in (S.AWAITING_APPROVAL, S.HELD):
            raise ApprovalRefused("WRONG_STATE", _state_message(state))
        if row["decision_json"] is None or row["decision_hash"] is None:
            raise ApprovalRefused("NO_DECISION", "the item has no decision yet")
        decision = Decision.model_validate_json(row["decision_json"])
        # --- bound to the decision_hash: data or policy changed since the decision -> refuse (never approve a stale decision) ---
        fresh = ids.decision_hash(decision.facts, policy.decision_hash_input)
        if fresh != row["decision_hash"] or decision.decision_hash != row["decision_hash"]:
            raise ApprovalRefused("STALE", "client data or policy changed since this decision was made, so the approval would not "
                                           "match what was decided. Re-run the request to re-decide, then approve again")
        _check_overrides(decision, state, overrides, reason)

        # --- first approval, or a retry after a provider-confirmed decline? ---
        pay = con.execute("SELECT * FROM payments WHERE item_id=?", (item_id,)).fetchone()
        prev_seq = con.execute("SELECT COALESCE(MAX(approval_seq),0) AS m FROM approvals WHERE item_id=? AND decision_hash=?",
                               (item_id, row["decision_hash"])).fetchone()["m"]
        retry = False
        if pay is not None:
            last = con.execute("SELECT * FROM payment_attempts WHERE payment_id=? AND attempt_no=?",
                               (pay["payment_id"], pay["current_attempt"])).fetchone()
            if state is S.HELD and pay["status"] == "FAILED" and last is not None and last["outcome"] == "DECLINED":
                retry = True
            else:
                raise ApprovalRefused("NOT_RETRYABLE", f"a payment already exists ({pay['status']}); a new attempt is allowed only after the "
                                                       "provider confirmed a decline before success. A reversal after success is never retried")
        elif state is S.HELD and not decision.flags and not con.execute(
                "SELECT 1 FROM exceptions WHERE item_id=? AND type='VENDOR_CONTAINED'", (item_id,)).fetchone():
            # a flag-less HELD item is legitimate only when it was held by vendor containment (re-approvable once the vendor's
            # exceptions are resolved) or after a declined payment (handled above)
            raise ApprovalRefused("WRONG_STATE", "the item is HELD with no flags and no failed payment; nothing to approve")
        elif prev_seq:
            raise ApprovalRefused("WRONG_STATE", "an approval already exists for this decision but no payment was created; run `resume`")

        # --- what will be paid, and where it is booked ---
        inv = Invoice.model_validate_json(row["payload_json"])
        vendor_id = decision.facts.vendor_id
        if not vendor_id or not inv.invoice_no_canonical or inv.amount_paise is None or inv.amount_paise <= 0:
            raise ApprovalRefused("INCOMPLETE", "cannot pay without a resolved vendor, an invoice number and a positive amount")
        if not decision.account:
            raise ApprovalRefused("NO_ACCOUNT", "no expense account is mapped for this invoice (ACCOUNT_UNMAPPED); "
                                                "fix the mapping in policy or the chart of accounts and re-run")

        # --- vendor containment, INSIDE the enqueue transaction ---
        contained = history.open_critical_for_vendor(vendor_id)
        if contained:
            if state is S.AWAITING_APPROVAL:
                transition(con, item_id=item_id, run_id=row["run_id"], session_id=session_id, expected_state=S.AWAITING_APPROVAL,
                           expected_version=row["version"], new_state=S.HELD, actor=ACTOR_SYSTEM, ts=now,
                           reason="VENDOR_CONTAINED: " + ", ".join(contained), metadata={"flag": "VENDOR_CONTAINED", "exceptions": contained})
            upsert_exceptions(con, run_id=row["run_id"], session_id=session_id, item_id=item_id, vendor_id=vendor_id, now=now, drafts=[
                ExceptionDraft(type="VENDOR_CONTAINED", severity=Severity.HIGH, blocking=True,
                               summary=f"Payment to {vendor_id} blocked: open critical exception(s) for this vendor",
                               details="Open: " + ", ".join(contained), recommended_action="Resolve the vendor's open exceptions with evidence, then approve again",
                               evidence_refs=[], related_refs=contained, origin_key=",".join(contained), item_id=item_id)])
        else:
            seq = prev_seq + 1
            approval_id = ids.approval_id(item_id, row["decision_hash"], seq)
            pay_key = ids.payment_key(row["client_id"], vendor_id, inv.invoice_no_canonical, inv.amount_paise, inv.currency)
            con.execute("INSERT INTO approvals(approval_id,item_id,decision_hash,status,reviewer,reason,overrides_json,requested_at,decided_at,"
                        "approval_seq) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (approval_id, item_id, row["decision_hash"], "APPROVED", reviewer, reason, json.dumps(overrides), now.isoformat(),
                         now.isoformat(), seq))
            append_event(con, run_id=row["run_id"], session_id=session_id, entity_type="approval", entity_id=approval_id, previous_state=None,
                         new_state="APPROVED", actor=reviewer, ts=now, reason=reason,
                         metadata={"item_id": item_id, "decision_hash": row["decision_hash"], "seq": seq, "overrides": overrides})
            if retry:
                if pay["payment_key"] != pay_key:
                    raise ApprovalRefused("KEY_MISMATCH", "the payment key changed; refusing to retry")     # payment_key is immutable
                attempt_no = pay["current_attempt"] + 1
                con.execute("UPDATE payments SET status='CREATED', current_attempt=?, updated_at=? WHERE payment_id=?",
                            (attempt_no, now.isoformat(), pay["payment_id"]))
                payment_id, prev_pay_state = pay["payment_id"], "FAILED"
            else:
                attempt_no, prev_pay_state = 1, None
                payment_id = ids.sha256_hex("payment", pay_key)[:16]
                con.execute("INSERT INTO payments(payment_id,item_id,approval_id,payment_key,vendor_id,provider,amount_paise,currency,status,"
                            "settlement_status,current_attempt,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (payment_id, item_id, approval_id, pay_key, vendor_id, provider_name, inv.amount_paise, inv.currency, "CREATED",
                             "NOT_AVAILABLE", 1, now.isoformat(), now.isoformat()))
            attempt_key = ids.attempt_key(pay_key, attempt_no)
            con.execute("INSERT INTO payment_attempts(attempt_key,payment_id,attempt_no,approval_id,provider_ref,step,outcome,last_error,"
                        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (attempt_key, payment_id, attempt_no, approval_id, None, "NONE", "PENDING", None, now.isoformat(), now.isoformat()))
            append_event(con, run_id=row["run_id"], session_id=session_id, entity_type="payment", entity_id=payment_id,
                         previous_state=prev_pay_state, new_state="CREATED", actor=ACTOR_SYSTEM, ts=now,
                         metadata={"attempt_key": attempt_key, "approval_id": approval_id, "attempt_no": attempt_no})
            if not retry:                                                   # the accrual is booked once per invoice, never per attempt
                journal.post_entry(con, journal.invoice_accrual(inv, decision, approval_id, now))
            transition(con, item_id=item_id, run_id=row["run_id"], session_id=session_id, expected_state=state, expected_version=row["version"],
                       new_state=S.APPROVED, actor=reviewer, ts=now, reason=reason,
                       metadata={"approval_id": approval_id, "decision_hash": row["decision_hash"], "overrides": overrides, "retry": retry})
            result = ApprovalResult("APPROVED", _approval(con.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()),
                                    payment_id, attempt_key, attempt_no, retry)
    if contained:
        raise ApprovalRefused("VENDOR_CONTAINED", "payments to this vendor are contained by open exception(s) " + ", ".join(contained) +
                              "; the item is held until they are resolved with evidence")
    crash_point("after_approval_commit")
    crash_point("after_payment_insert")
    assert result is not None
    return result


def reject_item(con: sqlite3.Connection, *, item_id: str, reviewer: str, reason: str | None, cfg: RunConfig, session_id: str,
                now: datetime) -> ApprovalResult:
    reviewer = check_reviewer(reviewer, cfg.approvals.get("approvers"))
    reason = (reason or "").strip()
    if not reason:
        raise ApprovalRefused("REASON_REQUIRED", "a rejection needs a written --reason")
    with tx(con):
        row = con.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ApprovalRefused("UNKNOWN_ITEM", f"no item {item_id}")
        state = S(row["state"])
        if state is S.REJECTED:
            prev = con.execute("SELECT * FROM approvals WHERE item_id=? AND status='REJECTED' ORDER BY approval_seq DESC LIMIT 1", (item_id,)).fetchone()
            if prev is not None:
                return ApprovalResult("ALREADY_REJECTED", _approval(prev))
        if state in APPROVED_STATES:
            raise ApprovalRefused("ALREADY_APPROVED", f"item is {state.value}: an approved item cannot be rejected "
                                                       "(a payment may be in flight)")
        if state not in (S.AWAITING_APPROVAL, S.HELD) or row["decision_hash"] is None:
            raise ApprovalRefused("WRONG_STATE", f"item is {state.value}: only items AWAITING_APPROVAL or HELD can be rejected")
        seq = con.execute("SELECT COALESCE(MAX(approval_seq),0)+1 AS n FROM approvals WHERE item_id=? AND decision_hash=?",
                          (item_id, row["decision_hash"])).fetchone()["n"]
        approval_id = ids.approval_id(item_id, row["decision_hash"], seq)
        con.execute("INSERT INTO approvals(approval_id,item_id,decision_hash,status,reviewer,reason,overrides_json,requested_at,decided_at,"
                    "approval_seq) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (approval_id, item_id, row["decision_hash"], "REJECTED", reviewer, reason, "[]", now.isoformat(), now.isoformat(), seq))
        append_event(con, run_id=row["run_id"], session_id=session_id, entity_type="approval", entity_id=approval_id, previous_state=None,
                     new_state="REJECTED", actor=reviewer, ts=now, reason=reason, metadata={"item_id": item_id, "seq": seq})
        transition(con, item_id=item_id, run_id=row["run_id"], session_id=session_id, expected_state=state, expected_version=row["version"],
                   new_state=S.REJECTED, actor=reviewer, ts=now, reason=reason, metadata={"approval_id": approval_id})
        return ApprovalResult("REJECTED", _approval(con.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()))


def approval_queue(con: sqlite3.Connection, run_id: str) -> tuple[list[dict], int]:
    """Items a human can act on now: AWAITING_APPROVAL, and HELD items whose flags are all overridable.
    Returns (rows, number of HELD items that cannot be approved at all)."""
    rows, blocked = [], 0
    for r in con.execute("SELECT * FROM items WHERE run_id=? AND state IN ('AWAITING_APPROVAL','HELD') ORDER BY input_name, row_no", (run_id,)):
        if r["decision_json"] is None:
            continue
        d = Decision.model_validate_json(r["decision_json"])
        never = [f for f in d.flags if f in NEVER]
        if r["state"] == "HELD" and (never or any(f not in set(d.overridable) for f in d.flags)) and d.flags:
            blocked += 1
            continue
        payload = json.loads(r["payload_json"] or "{}")
        exc = con.execute("SELECT COUNT(*) FROM exceptions WHERE (item_id=? OR (vendor_id IS NOT NULL AND vendor_id=?)) AND status NOT IN "
                          "('RESOLVED','FALSE_POSITIVE','ACCEPTED_RISK','CLOSED_NO_ACTION')", (r["item_id"], r["vendor_id"])).fetchone()[0]
        srcs = [s["provider"] + ":" + s["source_id"] for s in con.execute(
            "SELECT DISTINCT s.provider, s.source_id FROM item_evidence e JOIN sources s ON s.source_id=e.source_id "
            "WHERE e.item_id=? AND e.decision_hash=? ORDER BY s.source_id", (r["item_id"], r["decision_hash"]))]
        rows.append({"item_id": r["item_id"], "ref": r["source_ref"].split("#", 1)[-1], "state": r["state"], "payee": d.payee_legal_name,
                     "vendor_id": d.facts.vendor_id, "amount_paise": r["amount_paise"], "account": d.account,
                     "account_last4": payload.get("account_last4"), "bank_status": d.bank_status or {}, "flags": d.flags,
                     "overridable": d.overridable, "reasons": [x.text for x in d.reasons], "evidence": srcs, "open_exceptions": exc})
    return rows, blocked


def approve(item_id: str, reviewer: str, reason: str | None, overrides: list[str]) -> Approval:
    """Contract entry point (INTERFACES §9): bind the hash, enqueue the payment. Opens the run's database from $PAYPILOT_ROOT.
    It does NOT execute the payment: the CLI runs payments.executor after this returns (task A8)."""
    from paypilot.cli import runtime as rt                                          # lazy: the CLI layer owns root/config loading
    from paypilot.core.db import connect
    from paypilot.core.history import SqliteHistory
    from paypilot.core.lock import RunLock

    root = rt.start()
    paths = rt.storage_paths(root)
    con = connect(paths["db_path"], apply_schema=False)
    try:
        row = con.execute("SELECT i.run_id, r.client_id FROM items i JOIN runs r ON r.run_id=i.run_id WHERE i.item_id=?", (item_id,)).fetchone()
        if row is None:
            raise ApprovalRefused("UNKNOWN_ITEM", f"no item {item_id}")
        loaded = rt.load_all(row["client_id"], root)
        with RunLock(paths["lock_path"], wait_s=float(loaded.cfg.approvals.get("lock_wait_s", 10))):
            return approve_item(con, item_id=item_id, reviewer=reviewer, reason=reason, overrides=overrides, cfg=loaded.cfg, policy=loaded.policy,
                                history=SqliteHistory(con, loaded.data, row["client_id"], row["run_id"]),
                                provider_name=loaded.cfg.payments.get("provider", "fake"), session_id="api", now=rt.utcnow()).approval
    finally:
        con.close()
