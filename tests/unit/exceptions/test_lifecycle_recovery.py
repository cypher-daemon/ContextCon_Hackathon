from datetime import datetime, timezone

import pytest

from paypilot.exceptions.lifecycle import can_transition
from paypilot.exceptions.recovery import prepare_case
from paypilot.schemas import (ExceptionRecord, ExceptionStatus, Payment, PaymentStatus, RecoveryStatus,
                              SettlementStatus, Severity)


def _payment():
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    return Payment(payment_id="PAY-1", item_id="INV-1", approval_id="APP-1", payment_key="pp-key",
                   provider="fake", amount_paise=50_000, currency="INR", status=PaymentStatus.RETURNED,
                   settlement_status=SettlementStatus.CONFLICT, created_at=now, updated_at=now)


def _exception(**overrides):
    data = dict(type="PAYMENT_RETURNED", severity=Severity.CRITICAL, blocking=True,
                summary="Payment returned", details="Provider reports returned payment.",
                recommended_action="Contact bank/provider and request return.", evidence_refs=["src-1"],
                related_refs=[], origin_key="origin", item_id="INV-1", payment_id="PAY-1",
                open_recovery_case=True, exception_id="EXC-1", run_id="R-1",
                status=ExceptionStatus.CONTAINED, created_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
                updated_at=datetime(2026, 11, 10, tzinfo=timezone.utc))
    data.update(overrides)
    return ExceptionRecord(**data)


def test_exception_transitions_follow_documented_graph_and_require_resolution_evidence():
    assert can_transition(ExceptionStatus.DETECTED, ExceptionStatus.CLASSIFIED, [])
    assert can_transition(ExceptionStatus.CLASSIFIED, ExceptionStatus.CONTAINED, [])
    assert can_transition(ExceptionStatus.CONTAINED, ExceptionStatus.INVESTIGATING, [])
    assert can_transition(ExceptionStatus.INVESTIGATING, ExceptionStatus.ACTION_REQUIRED, [])
    assert not can_transition(ExceptionStatus.ACTION_REQUIRED, ExceptionStatus.RESOLVED, [])
    assert can_transition(ExceptionStatus.ACTION_REQUIRED, ExceptionStatus.RESOLVED, ["source-1"])
    assert not can_transition(ExceptionStatus.DETECTED, ExceptionStatus.RESOLVED, ["source-1"])
    assert not can_transition(ExceptionStatus.RESOLVED, ExceptionStatus.INVESTIGATING, ["source-1"])


def test_recovery_case_is_deterministic_and_prepares_human_action():
    exc, payment = _exception(), _payment()

    first = prepare_case(exc, payment)
    second = prepare_case(exc, payment)

    assert first == second
    assert first.status == RecoveryStatus.ACTION_PREPARED
    assert first.exception_id == exc.exception_id
    assert first.payment_id == payment.payment_id
    assert first.reason == exc.details
    assert first.requested_action == exc.recommended_action
    assert first.evidence_refs == ["src-1"]


@pytest.mark.parametrize("changes", [
    {"open_recovery_case": False},
    {"payment_id": "OTHER"},
    {"item_id": "OTHER"},
    {"recommended_action": ""},
])
def test_recovery_case_rejects_unlinked_or_unactionable_exceptions(changes):
    with pytest.raises(ValueError):
        prepare_case(_exception(**changes), _payment())
