"""RECONCILE operation (A12): each bank-statement line is reconciled against payments and payment history. Owner: lane A.

Uses lane C's pure `three_way` (reconciliation.match) for every comparison; this module only gathers the stored facts, picks the
strongest association for the line, and persists the outcome (settlement status, exceptions, recovery cases, vendor containment,
audit) in ONE transaction together with the item's PARSED -> RECONCILED transition.

Per line (SC-07): MATCHED (ref + amount) · CONFLICT/DUPLICATE_DEBIT (a later line matches an already-matched payment; CRITICAL +
recovery case; the vendor is contained) · CONFLICT/RETURNED_PAYMENT (returned money, from history, the provider or the line's own
text; CRITICAL; vendor contained; a recovery case only when PayPilot made the payment) · UNMATCHED (MEDIUM, non-blocking).
Reconciliation never creates a payment and never edits the books by itself.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from paypilot.context import StageContext
from paypilot.core import ids
from paypilot.core.audit import append_event
from paypilot.core.db import tx
from paypilot.core.exceptions_repo import add_exception
from paypilot.core.payments_repo import payment_from_row
from paypilot.core.state import transition
from paypilot.reconciliation.match import three_way
from paypilot.schemas import (BankLine, DateBasis, Decision, Facts, ItemState as S, Proposal, ProviderPayment, ProviderStatus, Reason,
                              ReconciliationResult, Severity, SettlementStatus, Source)

ACTOR = "system"
_RETURN_WORDS = ("RETURN", "REVERSAL", "REVERSED", "REFUND")
_RANK = {SettlementStatus.MATCHED: 3, SettlementStatus.CONFLICT: 2, SettlementStatus.POSSIBLE_MATCH: 1}


def _confirmed_payments(con: sqlite3.Connection, client_id: str) -> list[dict[str, Any]]:
    out = []
    for p in con.execute("SELECT p.*, i.decision_json FROM payments p JOIN items i ON i.item_id=p.item_id WHERE i.client_id=? AND p.status IN "
                         "('PROVIDER_CONFIRMED','RETURNED','REVERSED') ORDER BY p.created_at, p.payment_id", (client_id,)):
        att = con.execute("SELECT provider_ref FROM payment_attempts WHERE payment_id=? AND provider_ref IS NOT NULL "
                          "ORDER BY attempt_no DESC LIMIT 1", (p["payment_id"],)).fetchone()
        if att is None or not p["decision_json"]:
            continue
        out.append({"payment": payment_from_row(p), "decision": Decision.model_validate_json(p["decision_json"]), "ref": att["provider_ref"],
                    "vendor_id": p["vendor_id"]})
    return out


def _view(c: dict[str, Any]) -> ProviderPayment:
    p = c["payment"]
    st = {"PROVIDER_CONFIRMED": ProviderStatus.SUCCEEDED, "RETURNED": ProviderStatus.RETURNED, "REVERSED": ProviderStatus.RETURNED}[p.status.value]
    return ProviderPayment(ref=c["ref"], status=st, amount_paise=p.amount_paise, currency=p.currency, metadata={})


def _already_matched(con: sqlite3.Connection, run_id: str) -> dict[str, str]:
    """payment_id -> source_ref of the first earlier line of this run that was MATCHED to it."""
    out: dict[str, str] = {}
    for r in con.execute("SELECT source_ref, checks_json FROM items WHERE run_id=? AND state='RECONCILED' ORDER BY COALESCE(item_date,''), input_name, row_no",
                         (run_id,)):
        for ch in json.loads(r["checks_json"] or "[]"):
            d = ch.get("details") or {}
            if d.get("status") == "MATCHED" and d.get("payment_id"):
                out.setdefault(d["payment_id"], r["source_ref"])
    return out


def reconcile_line(con: sqlite3.Connection, ctx: StageContext, row: sqlite3.Row, line: BankLine) -> None:
    """Reconcile one statement line and persist the outcome. Idempotent per item (the state transition is a CAS)."""
    now = ctx.clock()
    history = list(ctx.data.history or [])
    # 1. the strongest association with a payment PayPilot made
    best: tuple[int, dict[str, Any], ReconciliationResult] | None = None
    for c in _confirmed_payments(con, ctx.client_id):
        hist = [h for h in history if h.vendor_id == c["vendor_id"] and h.invoice_no_canonical == c["decision"].facts.invoice_no_canonical]
        res = three_way(c["decision"], c["payment"], _view(c), [line], hist, ctx.policy)
        if line.source_ref in res.statement_refs:
            rank = _RANK.get(res.status, 0)
            if best is None or rank > best[0]:
                best = (rank, c, res)
    flags: set[str] = set()
    differences: dict[str, Any] = {}
    status = SettlementStatus.UNMATCHED
    payment_id = vendor_id = history_ref = duplicate_of = None
    made_by_us = False
    if best is not None:
        _, c, res = best
        payment_id, vendor_id, made_by_us = c["payment"].payment_id, c["vendor_id"], True
        status, flags, differences = res.status, set(res.flags), dict(res.differences)
        flags.discard("DUPLICATE_DEBIT")                       # the per-payment result sees only this line; duplicates are found across lines below
        first = _already_matched(con, ctx.run_id).get(payment_id)
        if first and first != line.source_ref:
            flags.add("DUPLICATE_DEBIT"); status = SettlementStatus.CONFLICT; duplicate_of = first
        elif not flags and status in (SettlementStatus.MATCHED, SettlementStatus.POSSIBLE_MATCH):
            pass
        elif flags:
            status = SettlementStatus.CONFLICT
    else:
        # 2. payments made outside PayPilot: the client's payment history
        res = three_way(None, None, None, [line], history, ctx.policy)
        if res.status != SettlementStatus.UNMATCHED:
            status, flags, differences = res.status, set(res.flags), dict(res.differences)
            refs = {line.bank_reference} - {None}
            rec = next((h for h in history if refs & {h.bank_reference, h.provider_ref}), None) or next(
                (h for h in history if h.amount_paise == line.amount_paise and abs((h.paid_on - line.date).days) <= 3), None)
            if rec is not None:
                history_ref, vendor_id = rec.ref, rec.vendor_id
    text = f"{line.descriptor_raw}".upper()
    if (any(w in text for w in _RETURN_WORDS) or line.direction == "CREDIT") and (best is not None or history_ref is not None):
        flags.add("RETURNED_PAYMENT"); status = SettlementStatus.CONFLICT
        differences.setdefault("statement_text", line.descriptor_raw)
    if best is None and history_ref is None and line.direction == "DEBIT":
        status = SettlementStatus.UNMATCHED

    flag_list = sorted(flags)
    reasons = [Reason(code=f, text=f"{f}: " + ("duplicate of " + duplicate_of if f == "DUPLICATE_DEBIT" and duplicate_of else
                                               "returned money" if f == "RETURNED_PAYMENT" else "statement differs from the instruction"))
               for f in flag_list]
    if status is SettlementStatus.UNMATCHED:
        reasons.append(Reason(code="UNMATCHED", text="no payment instruction or payment history matches this statement line"))
    facts = Facts(operation=ctx.operation, kind="BankLine", vendor_id=vendor_id, amount_paise=line.amount_paise, currency=line.currency,
                  date=line.date, flags=flag_list)
    decision = Decision(item_id=line.item_id, proposal=Proposal.RECONCILED, flags=flag_list, reasons=reasons, facts=facts,
                        decision_hash=ids.decision_hash(facts, ctx.policy.decision_hash_input))
    check = {"check_id": "reconcile", "status": "PASS" if status in (SettlementStatus.MATCHED, SettlementStatus.POSSIBLE_MATCH) and not flags else "FAIL",
             "flags": flag_list, "reasons": [r.text for r in reasons], "missing": [],
             "details": {"status": status.value, "payment_id": payment_id, "history_ref": history_ref, "duplicate_of": duplicate_of,
                         "differences": differences}, "evidence": []}
    src = Source(source_id=ids.source_id("bank_statement", line.source_ref, "statement.line", line.line_id), provider="bank_statement",
                 record_ref=line.source_ref, fact="statement.line", value=f"{line.direction} {line.amount_paise} {line.currency} {line.bank_reference or ''}".strip(),
                 field_or_passage=line.descriptor_raw, record_date=line.date, record_date_basis=DateBasis.RECORD_FIELD, retrieved_at=now)

    with tx(con):
        con.execute("INSERT OR IGNORE INTO sources(source_id,provider,record_ref,fact,value,field_or_passage,record_date,record_date_basis,"
                    "retrieved_at,limitations_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (src.source_id, src.provider, src.record_ref, src.fact, src.value, src.field_or_passage, src.record_date.isoformat(),
                     src.record_date_basis.value, src.retrieved_at.isoformat(), "[]"))
        con.execute("INSERT OR IGNORE INTO item_evidence(item_id,decision_hash,source_id,check_id) VALUES (?,?,?,?)",
                    (line.item_id, decision.decision_hash, src.source_id, "reconcile"))
        transition(con, item_id=line.item_id, run_id=ctx.run_id, session_id=ctx.session_id, expected_state=S.PARSED, expected_version=row["version"],
                   new_state=S.RECONCILED, actor=ACTOR, ts=now, reason=f"{status.value}" + (f" {flag_list}" if flag_list else ""),
                   metadata={"status": status.value, "flags": flag_list, "payment_id": payment_id, "history_ref": history_ref},
                   set_columns={"decision_json": decision.model_dump_json(), "decision_hash": decision.decision_hash,
                                "checks_json": json.dumps([check]), "vendor_id": vendor_id})
        if payment_id and status in (SettlementStatus.MATCHED, SettlementStatus.POSSIBLE_MATCH):
            con.execute("UPDATE payments SET settlement_status=?, updated_at=? WHERE payment_id=? AND settlement_status!=?",
                        (status.value, now.isoformat(), payment_id, status.value))
            pst = con.execute("SELECT status FROM payments WHERE payment_id=?", (payment_id,)).fetchone()["status"]
            append_event(con, run_id=ctx.run_id, session_id=ctx.session_id, entity_type="payment", entity_id=payment_id, previous_state=pst,
                         new_state=pst, actor=ACTOR, ts=now, reason=f"settlement {status.value} against {line.source_ref}",
                         metadata={"settlement_status": status.value, "statement_ref": line.source_ref})
        kw = dict(con=con, run_id=ctx.run_id, session_id=ctx.session_id, item_id=line.item_id, vendor_id=vendor_id, now=now,
                  payment_id=payment_id, origin_key=line.source_ref)
        if "DUPLICATE_DEBIT" in flags:
            add_exception(type_="DUPLICATE_DEBIT", severity=Severity.CRITICAL, recovery=True,
                          summary=f"Second debit {line.source_ref} for the same payment (first: {duplicate_of})",
                          details=f"{line.descriptor_raw}: {line.amount_paise} paise debited again with reference {line.bank_reference}",
                          action="Contact the bank/provider and request a return of the duplicate debit; the vendor is contained", **kw)
        if "RETURNED_PAYMENT" in flags or "REVERSED_PAYMENT" in flags:
            add_exception(type_="PAYMENT_RETURNED", severity=Severity.CRITICAL, recovery=made_by_us,
                          summary=f"Statement line {line.source_ref} shows a returned payment" + (f" (history {history_ref})" if history_ref else ""),
                          details=f"{line.descriptor_raw}; {differences}",
                          action=("Contact the bank/provider; follow the human-led recovery case" if made_by_us else
                                  "This payment was made outside PayPilot (no payment row, so no REVERSAL entry or recovery case): "
                                  "follow the client's own recovery process"), **kw)
        for f in ("AMOUNT_MISMATCH", "SETTLEMENT_MISMATCH", "WRONG_PAYEE_SUSPECTED", "DUPLICATE_PAYMENT"):
            if f in flags:
                add_exception(type_={"AMOUNT_MISMATCH": "AMOUNT_MISMATCH", "SETTLEMENT_MISMATCH": "SETTLEMENT_MISMATCH",
                                     "WRONG_PAYEE_SUSPECTED": "WRONG_PAYEE", "DUPLICATE_PAYMENT": "DUPLICATE_PAYMENT"}[f],
                              severity=Severity.CRITICAL, recovery=made_by_us, summary=f"{f} on {line.source_ref}", details=str(differences),
                              action="Investigate with the bank/provider; the vendor is contained", **{**kw, "origin_key": f"{line.source_ref}:{f}"})
        if status is SettlementStatus.UNMATCHED and line.direction == "DEBIT":
            add_exception(type_="UNMATCHED_PAYMENT", severity=Severity.MEDIUM, blocking=False,
                          summary=f"Statement line {line.source_ref} has no matching instruction or payment history",
                          details=f"{line.descriptor_raw}: {line.amount_paise} paise, reference {line.bank_reference}",
                          action="Identify the debit; if it is a payment, record it in the client's payment history", **kw)
