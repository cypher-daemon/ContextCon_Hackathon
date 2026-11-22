"""Deterministic recovery-case preparation (C7b)."""
from __future__ import annotations

from paypilot.core.ids import sha256_hex
from paypilot.schemas import ExceptionRecord, Payment, RecoveryCase, RecoveryStatus


def prepare_case(exc: ExceptionRecord, payment: Payment) -> RecoveryCase:
    """Prepare a human-led recovery case for a mapped exception and its payment."""
    if not exc.open_recovery_case:
        raise ValueError(f"exception {exc.exception_id} is not marked for recovery")
    if exc.payment_id and exc.payment_id != payment.payment_id:
        raise ValueError("recovery payment does not match the exception")
    if exc.item_id and exc.item_id != payment.item_id:
        raise ValueError("recovery payment belongs to a different item")
    if not exc.recommended_action.strip():
        raise ValueError("recovery case needs a recommended human action")

    recovery_id = "RC-" + sha256_hex(exc.exception_id, payment.payment_id, exc.type, exc.origin_key)[:16]
    return RecoveryCase(
        recovery_id=recovery_id, exception_id=exc.exception_id, payment_id=payment.payment_id,
        reason=exc.details or exc.summary, requested_action=exc.recommended_action,
        status=RecoveryStatus.ACTION_PREPARED, evidence_refs=list(exc.evidence_refs),
    )
