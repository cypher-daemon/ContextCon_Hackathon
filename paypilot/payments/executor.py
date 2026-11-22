"""payments.executor (A8): step-recorded payment execution. Owner: lane A.

Every step is persisted BEFORE the side effect it names, in the same transaction as its audit event:

  NONE -> CREATE_SENT (written before provider.create) -> REF_SAVED (provider_ref written before confirm)
       -> CONFIRM_SENT (written before provider.confirm) -> DONE

A crash anywhere is recovered by looking at the step: a missing outcome is never "paid", never "retry with a new key". An attempt that
may have reached the provider is only ever resolved by retrieve / find_by_attempt_key with the SAME key. PAID is written only on a
documented terminal success (ProviderStatus.SUCCEEDED); non-terminal -> SUBMITTED / PAYMENT_IN_PROGRESS.
"""
from __future__ import annotations

import hmac
import json
import sqlite3
from datetime import datetime
from typing import Any, Callable

from paypilot.context import ProviderStatusUnknown, StageContext
from paypilot.core import ids, journal
from paypilot.core.audit import append_event
from paypilot.core.crash import crash_point
from paypilot.core.db import connect, tx
from paypilot.core.exceptions_repo import add_exception
from paypilot.core.env import bank_hmac_key
from paypilot.core.payments_repo import get_payment
from paypilot.core.state import transition
from paypilot.io.client_data import read_raw_bank
from paypilot.io.fingerprint import fingerprint
from paypilot.payments.provider import ProviderRejected, ProviderTimeout, build_provider
from paypilot.schemas import (Decision, Invoice, ItemState as S, Payment, ProviderPayment, ProviderStatus, RunConfig, Severity)

ACTOR = "system"
TERMINAL_OUTCOMES = ("SUCCEEDED", "DECLINED")


class _Ctx:
    """What the step functions need; built from a StageContext or from begin_recovery's plain arguments."""

    def __init__(self, con: sqlite3.Connection, provider: Any, cfg: RunConfig, clock: Callable[[], datetime], session_id: str):
        self.con, self.provider, self.cfg, self.clock, self.session_id = con, provider, cfg, clock, session_id


# ---------------- small persistence helpers (each is one transaction) ----------------
def _rows(con: sqlite3.Connection, payment_id: str):
    pay = con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone()
    att = con.execute("SELECT * FROM payment_attempts WHERE payment_id=? AND attempt_no=?", (payment_id, pay["current_attempt"])).fetchone()
    item = con.execute("SELECT * FROM items WHERE item_id=?", (pay["item_id"],)).fetchone()
    return pay, att, item


def _payment_event(c: _Ctx, pay, run_id: str, new_status: str, reason: str | None = None, **meta) -> None:
    c.con.execute("UPDATE payments SET status=?, updated_at=? WHERE payment_id=?", (new_status, c.clock().isoformat(), pay["payment_id"]))
    append_event(c.con, run_id=run_id, session_id=c.session_id, entity_type="payment", entity_id=pay["payment_id"],
                 previous_state=pay["status"], new_state=new_status, actor=ACTOR, ts=c.clock(), reason=reason, metadata=meta)


def _set_attempt(c: _Ctx, att, **cols) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    c.con.execute(f"UPDATE payment_attempts SET {sets}, updated_at=? WHERE attempt_key=?", (*cols.values(), c.clock().isoformat(), att["attempt_key"]))


def _item_to(c: _Ctx, item, new: S, reason: str | None = None, **meta) -> Any:
    """CAS the item to `new`, re-reading the row so chained transitions in one transaction see the new version."""
    cur = c.con.execute("SELECT state, version, run_id FROM items WHERE item_id=?", (item["item_id"],)).fetchone()
    transition(c.con, item_id=item["item_id"], run_id=cur["run_id"], session_id=c.session_id, expected_state=S(cur["state"]),
               expected_version=cur["version"], new_state=new, actor=ACTOR, ts=c.clock(), reason=reason, metadata=meta or None)
    return c.con.execute("SELECT * FROM items WHERE item_id=?", (item["item_id"],)).fetchone()


# ---------------- bank details at payout time (FAILURE_MODES #49) ----------------
class BankChanged(Exception):
    pass


def _bank_for_payout(client_id: str, vendor_id: str, decision: Decision, inv: Invoice) -> dict[str, str]:
    """Re-read the vendor-master row and check HMAC(raw) == the approved fingerprint. Raw number: in memory only."""
    raw, ifsc, legal = read_raw_bank(client_id, vendor_id)
    approved = decision.facts.account_fingerprint          # bound to the approved decision only; never fall back to the invoice's own value
    if not raw or approved is None:
        raise BankChanged("no approved bank fingerprint for this payout; re-decide and re-approve")
    fp, _ = fingerprint(raw, bank_hmac_key())
    if not hmac.compare_digest(fp or "", approved):
        raise BankChanged("the vendor-master bank account changed between approval and execution")
    if not (ifsc or "").strip():
        raise BankChanged("the vendor master has no IFSC for this vendor")
    return {"account_number": raw, "ifsc": ifsc, "vendor_name": legal or ""}


def _metadata(pay, item, inv: Invoice, bank: dict[str, str], att, provider: Any) -> dict:
    md = {"attempt_key": att["attempt_key"], "payment_key": pay["payment_key"], "vendor_id": pay["vendor_id"], "item_id": item["item_id"],
          "invoice_no": inv.invoice_no_raw, "vendor_name": bank["vendor_name"], "ifsc": bank["ifsc"]}
    if getattr(provider, "needs_bank_details", False):
        md["account_number"] = bank["account_number"]          # in memory only: never logged, stored or hashed
    return md


# ---------------- the steps ----------------
def _uncertain(c: _Ctx, pay, att, item, why: str) -> None:
    """The outcome is unknown: the money may have moved. Record it; never retry with a new key."""
    run_id = item["run_id"]
    with tx(c.con):
        if pay["status"] != "PAYMENT_UNCERTAIN":
            _payment_event(c, pay, run_id, "PAYMENT_UNCERTAIN", why, attempt_key=att["attempt_key"])
        _set_attempt(c, att, outcome="UNKNOWN", last_error=why[:500])
        if item["state"] == S.PAYMENT_IN_PROGRESS.value:
            item = _item_to(c, item, S.PAYMENT_UNCERTAIN, why)
        add_exception(c.con, run_id=run_id, session_id=c.session_id, item_id=item["item_id"], vendor_id=pay["vendor_id"], now=c.clock(),
                      type_="PROVIDER_OUTCOME_UNKNOWN", severity=Severity.HIGH, payment_id=pay["payment_id"],
                      summary=f"Payment outcome unknown for {pay['payment_key']}: {why}",
                      details=f"attempt {att['attempt_key']} step {att['step']}; the provider may already have paid",
                      action="Reconcile with the provider (same key); never create a new payment while the outcome is unknown",
                      origin_key=att["attempt_key"])


def _begin(c: _Ctx, pay, att, item) -> bool:
    """Step NONE -> CREATE_SENT. Returns False if the payout must not go ahead (nothing was sent to the provider)."""
    run_id = item["run_id"]
    inv = Invoice.model_validate_json(item["payload_json"])
    decision = Decision.model_validate_json(item["decision_json"])
    try:
        bank = _bank_for_payout(item["client_id"], pay["vendor_id"], decision, inv)
    except BankChanged as e:
        with tx(c.con):                                      # refused BEFORE any provider call: confirmed "not sent" -> a fresh decision + approval
            _payment_event(c, pay, run_id, "FAILED", str(e), attempt_key=att["attempt_key"])
            _set_attempt(c, att, step="DONE", outcome="DECLINED", last_error=str(e))
            _item_to(c, item, S.HELD, "BANK_CHANGE_REQUEST: " + str(e))
            add_exception(c.con, run_id=run_id, session_id=c.session_id, item_id=item["item_id"], vendor_id=pay["vendor_id"], now=c.clock(),
                          type_="BANK_CHANGE_REQUEST", severity=Severity.HIGH, payment_id=pay["payment_id"], summary=str(e),
                          details="Refused before any provider call", action="Verify the bank change through the client's process, then re-run and approve again",
                          origin_key=att["attempt_key"])
        return False
    c._bank = bank                                           # type: ignore[attr-defined]
    c._inv = inv                                             # type: ignore[attr-defined]
    with tx(c.con):                                          # persist BEFORE the provider call
        _set_attempt(c, att, step="CREATE_SENT")
        _payment_event(c, pay, run_id, "SUBMITTED", None, attempt_key=att["attempt_key"], step="CREATE_SENT")
        if item["state"] == S.APPROVED.value:
            _item_to(c, item, S.PAYMENT_IN_PROGRESS, "payment submitted", attempt_key=att["attempt_key"])
    crash_point("after_create_sent")
    return True


def _save_ref(c: _Ctx, pay, att, item, pp: ProviderPayment) -> None:
    with tx(c.con):                                          # provider_ref is persisted BEFORE confirm
        _set_attempt(c, att, provider_ref=pp.ref, step="REF_SAVED")
        append_event(c.con, run_id=item["run_id"], session_id=c.session_id, entity_type="payment", entity_id=pay["payment_id"],
                     previous_state=pay["status"], new_state=pay["status"], actor=ACTOR, ts=c.clock(), reason="provider_ref saved",
                     metadata={"provider_ref": pp.ref, "attempt_key": att["attempt_key"]})
    crash_point("after_ref_saved")


def _close_uncertain(c: _Ctx, pay, item, pp: ProviderPayment) -> None:
    """The provider has now given a terminal answer: the PROVIDER_OUTCOME_UNKNOWN exception is resolved, with the provider's reference as
    evidence (this lifts the vendor containment it caused). Runs inside the settle transaction."""
    for e in c.con.execute("SELECT * FROM exceptions WHERE payment_id=? AND type='PROVIDER_OUTCOME_UNKNOWN' AND status NOT IN "
                           "('RESOLVED','FALSE_POSITIVE','ACCEPTED_RISK','CLOSED_NO_ACTION')", (pay["payment_id"],)).fetchall():
        c.con.execute("UPDATE exceptions SET status='RESOLVED', resolution_code='RECONCILED', resolution_note=?, resolved_by=?, resolved_at=?, "
                      "updated_at=?, evidence_refs_json=? WHERE exception_id=?",
                      (f"provider reports {pp.status.value} for {pp.ref}", ACTOR, c.clock().isoformat(), c.clock().isoformat(),
                       json.dumps([f"{c.provider.name}:{pp.ref}"]), e["exception_id"]))
        append_event(c.con, run_id=item["run_id"], session_id=c.session_id, entity_type="exception", entity_id=e["exception_id"],
                     previous_state=e["status"], new_state="RESOLVED", actor=ACTOR, ts=c.clock(),
                     reason="reconciled with the provider using the same key", metadata={"provider_ref": pp.ref, "provider_status": pp.status.value})


def _settle(c: _Ctx, pay, att, item, pp: ProviderPayment) -> None:
    """Map a provider status to state. PAID only on SUCCEEDED."""
    run_id = item["run_id"]
    st = pp.status
    if st is ProviderStatus.SUCCEEDED:
        inv = Invoice.model_validate_json(item["payload_json"])
        decision = Decision.model_validate_json(item["decision_json"])
        with tx(c.con):                                      # PROVIDER_CONFIRMED + attempt DONE + item PAID + INVOICE_PAYMENT journal: ONE txn
            _payment_event(c, pay, run_id, "PROVIDER_CONFIRMED", None, provider_ref=pp.ref, attempt_key=att["attempt_key"])
            _set_attempt(c, att, provider_ref=pp.ref, step="DONE", outcome="SUCCEEDED", last_error=None)
            _item_to(c, item, S.PAID, f"provider confirmed {pp.ref}", provider_ref=pp.ref)
            journal.post_entry(c.con, journal.invoice_payment(inv, decision, att["approval_id"], att["attempt_key"], c.clock()))
            _close_uncertain(c, pay, item, pp)
        crash_point("after_paid_commit")
    elif st in (ProviderStatus.DECLINED, ProviderStatus.CANCELED, ProviderStatus.RETURNED):
        # a decline, or a reversal BEFORE success: confirmed not paid -> a fresh decision and a new approval (seq n+1) may retry
        with tx(c.con):
            _payment_event(c, pay, run_id, "FAILED", f"provider {st.value}", provider_ref=pp.ref, attempt_key=att["attempt_key"])
            _set_attempt(c, att, provider_ref=pp.ref, step="DONE", outcome="DECLINED", last_error=f"provider {st.value}")
            it = _item_to(c, item, S.FAILED, f"provider {st.value}")
            _item_to(c, it, S.HELD, "payment failed: needs fresh checks and a new approval")
            _close_uncertain(c, pay, item, pp)
            add_exception(c.con, run_id=run_id, session_id=c.session_id, item_id=item["item_id"], vendor_id=pay["vendor_id"], now=c.clock(),
                          type_="PAYMENT_FAILED", severity=Severity.MEDIUM, payment_id=pay["payment_id"], blocking=False,
                          summary=f"Provider {st.value} payment {pay['payment_key']} (attempt {att['attempt_no']})",
                          details=f"provider_ref {pp.ref}", action="Fresh checks, then a new approval (seq n+1) creates attempt n+1",
                          origin_key=att["attempt_key"])
    else:                                                    # PROCESSING / REQUIRES_CONFIRMATION: not terminal, never PAID
        with tx(c.con):
            _set_attempt(c, att, provider_ref=pp.ref, outcome="PENDING")
            if pay["status"] in ("CREATED", "PAYMENT_UNCERTAIN"):
                _payment_event(c, pay, run_id, "SUBMITTED", f"provider {st.value}", provider_ref=pp.ref)


def _reverse_after_success(c: _Ctx, pay, att, item, pp: ProviderPayment) -> None:
    """A11b: PROVIDER_CONFIRMED then RETURNED. One txn: payment RETURNED, item PAID -> HELD, REVERSAL entry, CRITICAL exception +
    recovery case, audit. The open CRITICAL exception contains the vendor. There is no retry path."""
    run_id = item["run_id"]
    with tx(c.con):
        _payment_event(c, pay, run_id, "RETURNED", "returned after confirmed success", provider_ref=pp.ref, attempt_key=att["attempt_key"])
        _item_to(c, item, S.HELD, "REVERSED_PAYMENT: the provider returned the money after confirming it")
        journal.post_entry(c.con, journal.reversal_of_payment(c.con, item["item_id"], c.clock()))
        add_exception(c.con, run_id=run_id, session_id=c.session_id, item_id=item["item_id"], vendor_id=pay["vendor_id"], now=c.clock(),
                      type_="PAYMENT_RETURNED", severity=Severity.CRITICAL, payment_id=pay["payment_id"], recovery=True,
                      summary=f"Payment {pay['payment_key']} was returned after provider-confirmed success ({pp.ref})",
                      details="Money came back after PROVIDER_CONFIRMED. The vendor is contained; the invoice is HELD and is never paid again automatically.",
                      action="Contact the bank/provider; follow the human-led recovery case", origin_key=att["attempt_key"])


# ---------------- the attempt state machine ----------------
def _advance(c: _Ctx, payment_id: str) -> None:
    pay, att, item = _rows(c.con, payment_id)
    if pay["status"] in ("FAILED", "RETURNED", "REVERSED"):
        return
    if pay["status"] == "PROVIDER_CONFIRMED":                 # already paid: only watch for a reversal after success
        try:
            pp = c.provider.retrieve(att["provider_ref"])
        except (ProviderTimeout, ProviderStatusUnknown, KeyError):
            return                                            # reversal check is best-effort; the payment stays confirmed
        if pp.status is ProviderStatus.RETURNED:
            _reverse_after_success(c, pay, att, item, pp)
        return
    if att["outcome"] in TERMINAL_OUTCOMES:
        return
    step, fresh = att["step"], False
    if getattr(c.provider, "needs_bank_details", False):      # lets the adapter rebuild the identical create body on a replay (memory only)
        c.provider.context_for = lambda attempt_key: _replay_context(c, payment_id, attempt_key)
    try:
        if step == "NONE":
            if not _begin(c, pay, att, item):
                return
            pay, att, item = _rows(c.con, payment_id)
            step, fresh = "CREATE_SENT", True
        if step == "CREATE_SENT":
            if fresh:
                inv, bank = c._inv, c._bank                    # type: ignore[attr-defined]
                pp = c.provider.create(att["attempt_key"], pay["amount_paise"], pay["currency"], _metadata(pay, item, inv, bank, att, c.provider))
                crash_point("after_create_response_before_ref")
            else:                                              # may have reached the provider: look it up by the SAME key, never create
                pp = c.provider.find_by_attempt_key(att["attempt_key"])
                if pp is None:
                    return _uncertain(c, pay, att, item, "no provider record found for this attempt key (create may or may not have been sent)")
            _save_ref(c, pay, att, item, pp)
            pay, att, item = _rows(c.con, payment_id)
            step = "REF_SAVED"
        if step in ("REF_SAVED", "CONFIRM_SENT"):
            # The provider reference is already known, so a failed READ-ONLY lookup is NOT an unknown outcome: the state stays as it is
            # and the next run / resume simply asks again. A timeout in `confirm` (a money-moving call) is different: it falls through
            # to the handler below and becomes PAYMENT_UNCERTAIN. Only a failed create or confirm is ever uncertain.
            try:
                pp = c.provider.retrieve(att["provider_ref"])
            except ProviderTimeout:
                return
            if pp.status is ProviderStatus.REQUIRES_CONFIRMATION:
                if step == "REF_SAVED":
                    with tx(c.con):                            # persisted BEFORE the confirm call
                        _set_attempt(c, att, step="CONFIRM_SENT")
                crash_point("after_confirm_sent")
                pp = c.provider.confirm(att["provider_ref"], ids.step_key(att["attempt_key"], "confirm"))
                crash_point("after_confirm_response_before_commit")
            return _settle(c, pay, att, item, pp)
    except (ProviderTimeout, ProviderRejected, BankChanged) as e:
        pay, att, item = _rows(c.con, payment_id)
        return _uncertain(c, pay, att, item, f"{type(e).__name__}: {e}")
    except ProviderStatusUnknown as e:
        pay, att, item = _rows(c.con, payment_id)
        return _uncertain(c, pay, att, item, f"unrecognised provider status: {e}")


def _replay_context(c: _Ctx, payment_id: str, attempt_key: str) -> dict:
    pay, att, item = _rows(c.con, payment_id)
    inv = Invoice.model_validate_json(item["payload_json"])
    decision = Decision.model_validate_json(item["decision_json"])
    bank = _bank_for_payout(item["client_id"], pay["vendor_id"], decision, inv)    # re-fingerprinted again: a changed account is never replayed
    return {"amount_paise": pay["amount_paise"], "currency": pay["currency"], "metadata": _metadata(pay, item, inv, bank, att, c.provider),
            "attempt_created_at": datetime.fromisoformat(att["created_at"])}


def execute(payment: Payment, provider: Any, ctx: StageContext) -> Payment:
    """Step-recorded payment execution (INTERFACES §9). `provider=None` builds the adapter named on the payment row."""
    con = connect(ctx.cfg.storage["db_path"], apply_schema=False)
    try:
        prov = provider or build_provider(payment.provider, ctx.cfg)
        _advance(_Ctx(con, prov, ctx.cfg, ctx.clock, ctx.session_id), payment.payment_id)
        return get_payment(con, payment.payment_id)
    finally:
        con.close()
