"""Pure classification of decision and reconciliation flags (C7a)."""
from __future__ import annotations

from typing import Any

from paypilot.core.ids import sha256_hex
from paypilot.exceptions.map import DEFAULT_EXCEPTION_MAP
from paypilot.flags import Flag
from paypilot.schemas import (Decision, ExceptionDraft, Payment, PaymentStatus, ReconciliationResult,
                              Severity, WorkItem)

_ACTIONS = {
    "DUPLICATE_INVOICE": "Review the cited prior invoice; do not pay until the duplicate is resolved.",
    "DUPLICATE_TRANSACTION": "Review the cited bank transaction; override only with a documented reason.",
    "ALREADY_PAID": "Verify the prior payment record; do not submit another payment.",
    "IDENTITY_UNRESOLVED": "Add or verify the vendor master record, then rerun the item.",
    "IDENTITY_AMBIGUOUS": "Obtain authoritative vendor identity details before proceeding.",
    "BANK_CHANGE_REQUEST": "Verify bank changes through the client's independent process; do not update automatically.",
    "IFSC_INVALID": "Correct the vendor bank details through the client's verification process.",
    "ACCOUNT_UNMAPPED": "Add an approved vendor default account or category mapping, then rerun.",
    "PO_MISMATCH": "Review the purchase order and obtain an authorized override if appropriate.",
    "PAYMENT_RETURNED": "Contact the bank/provider and follow the human-led recovery process.",
    "DUPLICATE_DEBIT": "Contain the vendor and investigate the bank/provider records.",
    "PROVIDER_OUTCOME_UNKNOWN": "Reconcile the provider outcome; never submit using a new key while uncertain.",
}


def _map(policy: Any) -> dict[str, Any]:
    configured = getattr(getattr(policy, "exceptions", None), "map", {}) or {}
    result = dict(DEFAULT_EXCEPTION_MAP)
    result.update({flag.value if isinstance(flag, Flag) else str(flag): value for flag, value in configured.items()})
    return result


def classify(item: WorkItem | None, decision: Decision | None, recon: ReconciliationResult | None,
             payment: Payment | None, policy: Any) -> list[ExceptionDraft]:
    """Create deterministic drafts without persistence or external side effects."""
    mappings = _map(policy)
    flags = set(decision.flags if decision else ())
    if recon:
        flags.update(recon.flags)
    if payment:
        if payment.status == PaymentStatus.PAYMENT_UNCERTAIN:
            flags.add(Flag.PAYMENT_UNCERTAIN.value)
        elif payment.status == PaymentStatus.RETURNED:
            flags.add(Flag.RETURNED_PAYMENT.value)
        elif payment.status == PaymentStatus.REVERSED:
            flags.add(Flag.REVERSED_PAYMENT.value)

    after_payment = (recon is not None or (payment is not None and payment.status in {
        PaymentStatus.PROVIDER_CONFIRMED, PaymentStatus.PAYMENT_UNCERTAIN,
        PaymentStatus.RETURNED, PaymentStatus.REVERSED,
    }))
    decision_reasons = {reason.code: reason.text for reason in decision.reasons} if decision else {}
    evidence_refs = set(decision.evidence_refs if decision else ())
    if recon:
        evidence_refs.update(source.source_id for source in recon.evidence)
    related_refs = set(recon.statement_refs if recon else ())
    if item:
        related_refs.add(item.source_ref)
    if recon and recon.provider_ref:
        related_refs.add(recon.provider_ref)
    if payment:
        related_refs.add(payment.payment_id)

    item_id = decision.item_id if decision else (item.item_id if item else None)
    payment_id = payment.payment_id if payment else None
    drafts: list[ExceptionDraft] = []
    for flag in sorted(str(value) for value in flags):
        if flag == Flag.PERSISTENCE_FAILURE.value:
            continue  # always emitted to stderr/logging, never recorded as an exception row
        mapping = mappings.get(flag)
        if mapping is None:
            raise ValueError(f"no exception mapping configured for flag {flag}")
        severity = mapping.severity_post if after_payment else mapping.severity_pre
        reason_text = decision_reasons.get(flag, "")
        extra = ""
        if recon and recon.flags and flag in recon.flags:
            extra = f" Reconciliation status: {recon.status.value}; differences: {recon.differences}."
        if payment and flag in {Flag.PAYMENT_UNCERTAIN.value, Flag.RETURNED_PAYMENT.value,
                                Flag.REVERSED_PAYMENT.value}:
            extra += f" Payment status: {payment.status.value}; provider: {payment.provider}."
        details = (reason_text + extra).strip() or f"Detected flag: {flag}."
        type_ = mapping.type
        origin_key = sha256_hex("exception", item_id or "", payment_id or "", type_, flag,
                                decision.decision_hash if decision else "", recon.subject_ref if recon else "")
        drafts.append(ExceptionDraft(
            type=type_, severity=severity, blocking=mapping.blocking,
            summary=f"{type_}: {flag.replace('_', ' ').lower()}.", details=details,
            recommended_action=_ACTIONS.get(type_, "Review the evidence and resolve the underlying issue safely."),
            evidence_refs=sorted(evidence_refs), related_refs=sorted(related_refs), origin_key=origin_key,
            item_id=item_id, payment_id=payment_id,
            open_recovery_case=mapping.recovery_case and after_payment,
        ))
    return drafts
