from datetime import datetime, timezone

from paypilot.core.config import load_config
from paypilot.exceptions.classify import classify
from paypilot.exceptions.map import DEFAULT_EXCEPTION_MAP
from paypilot.flags import Flag
from paypilot.policy_config import ExceptionMapping
from paypilot.schemas import (BankLine, Decision, ExceptionDraft, Facts, Payment, Proposal, Reason, Severity,
                             PaymentStatus, SettlementStatus)


def _item():
    return BankLine(item_id="BL-003", source_ref="bank_lines.json#BL-003", line_id="BL-003",
                    date="2026-10-07", descriptor_raw="AWS EMEA", descriptor_key="AWS EMEA",
                    amount_paise=1_850_000, currency="INR", direction="DEBIT", transaction_fingerprint="fp")


def _decision(flag="DUPLICATE_TRANSACTION"):
    return Decision(item_id="BL-003", proposal=Proposal.HELD, flags=[flag],
                    reasons=[Reason(code=flag, text="Possible duplicate of BL-002", rule_id="R11",
                                     evidence_refs=["src-1"])], evidence_refs=["src-1"], rule_ids=["R11"],
                    overridable=[flag] if flag == "DUPLICATE_TRANSACTION" else [],
                    facts=Facts(operation="POST_BANK_LINES", kind="BankLine", vendor_id="V-0002",
                                amount_paise=1_850_000, currency="INR", date="2026-10-07", flags=[flag]),
                    decision_hash="decision-hash")


def _policy():
    return load_config("northbay")[1]


def test_duplicate_transaction_produces_high_blocking_draft_with_evidence():
    result = classify(_item(), _decision(), None, None, _policy())

    assert len(result) == 1
    draft = result[0]
    assert isinstance(draft, ExceptionDraft)
    assert (draft.type, draft.severity, draft.blocking) == ("DUPLICATE_TRANSACTION", Severity.HIGH, True)
    assert draft.evidence_refs == ["src-1"]
    assert draft.related_refs == ["bank_lines.json#BL-003"]
    assert draft.item_id == "BL-003"
    assert draft.origin_key


def test_payment_reconciliation_uses_post_severity_and_opens_recovery_case():
    policy = _policy()
    policy = policy.model_copy(update={"exceptions": policy.exceptions.model_copy(update={"map": {
        "DUPLICATE_PAYMENT": ExceptionMapping(type="DUPLICATE_PAYMENT", severity_pre="HIGH",
                                               severity_post="CRITICAL", blocking=True, recovery_case=True)
    }})})
    decision = _decision("DUPLICATE_PAYMENT")
    payment = Payment(payment_id="PAY-1", item_id="BL-003", approval_id="APP-1", payment_key="key",
                      provider="fake", amount_paise=1_850_000, currency="INR", status=PaymentStatus.PROVIDER_CONFIRMED,
                      settlement_status=SettlementStatus.PENDING,
                      created_at=datetime(2026, 10, 7, tzinfo=timezone.utc),
                      updated_at=datetime(2026, 10, 7, tzinfo=timezone.utc))

    result = classify(_item(), decision, None, payment, policy)

    assert result[0].severity == Severity.CRITICAL
    assert result[0].open_recovery_case is True
    assert "PAY-1" in result[0].related_refs


def test_persistence_failure_is_never_persisted_as_exception():
    result = classify(None, None, None, None, _policy())
    assert result == []


def test_default_exception_mappings_cover_every_recordable_flag():
    assert set(DEFAULT_EXCEPTION_MAP) == {flag.value for flag in Flag if flag is not Flag.PERSISTENCE_FAILURE}
