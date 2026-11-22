from datetime import date, datetime, timezone

from paypilot.core.config import load_config
from paypilot.reconciliation.match import three_way
from paypilot.schemas import (BankLine, Decision, Facts, HistoryRecord, Payment, PaymentStatus, Proposal,
                              ProviderPayment, ProviderStatus, Reason, SettlementStatus)


NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)


def _policy():
    return load_config("northbay")[1]


def _decision(**fact_overrides):
    facts = dict(operation="PAY_INVOICES", kind="Invoice", vendor_id="V-0003", amount_paise=4_800_000,
                  currency="INR", date=date(2026, 11, 3), invoice_no_canonical="DLV20262700418", flags=[])
    facts.update(fact_overrides)
    return Decision(item_id="INV-201", proposal=Proposal.AWAITING_APPROVAL, facts=Facts(**facts),
                    payee_legal_name="Delhivery Limited", decision_hash="decision-hash")


def _payment(**overrides):
    data = dict(payment_id="PAY-1", item_id="INV-201", approval_id="APP-1", payment_key="pp-key",
                provider="fake", amount_paise=4_800_000, currency="INR", status=PaymentStatus.PROVIDER_CONFIRMED,
                settlement_status=SettlementStatus.NOT_AVAILABLE, created_at=NOW, updated_at=NOW)
    data.update(overrides)
    return Payment(**data)


def _provider(**overrides):
    data = dict(ref="pout-1", status=ProviderStatus.SUCCEEDED, amount_paise=4_800_000,
                currency="INR", metadata={"vendor_id": "V-0003"})
    data.update(overrides)
    return ProviderPayment(**data)


def _line(ref="pout-1", *, amount=4_800_000, currency="INR", day=date(2026, 11, 10),
          direction="DEBIT", line_id="ST-1"):
    return BankLine(item_id=line_id, source_ref=f"statement.json#{line_id}", line_id=line_id,
                    date=day, descriptor_raw="DELHIVERY", descriptor_key="DELHIVERY", amount_paise=amount,
                    currency=currency, direction=direction, bank_reference=ref,
                    transaction_fingerprint=f"fp-{line_id}")


def _history(ref="HIST-1", *, status="PAID", amount=4_800_000, vendor="V-0003",
             invoice="DLV20262700418", provider_ref="pout-1", bank_reference=None):
    return HistoryRecord(ref=ref, vendor_id=vendor, invoice_no_canonical=invoice, amount_paise=amount,
                         currency="INR", paid_on=date(2026, 11, 10), provider_ref=provider_ref,
                         bank_reference=bank_reference, status=status, record_ref=f"payment_history.csv#{ref}")


def test_exact_provider_reference_amount_and_currency_match():
    result = three_way(_decision(), _payment(), _provider(), [_line()], None, _policy())

    assert result.status == SettlementStatus.MATCHED
    assert result.flags == []
    assert result.statement_refs == ["statement.json#ST-1"]
    assert result.provider_ref == "pout-1"
    assert result.instruction_ref == "pp-key"


def test_duplicate_statement_debits_flag_conflict():
    result = three_way(_decision(), _payment(), _provider(), [_line(), _line(line_id="ST-2")], None, _policy())

    assert result.status == SettlementStatus.CONFLICT
    assert result.flags == ["DUPLICATE_DEBIT"]
    assert result.differences["duplicate_debit_refs"] == ["statement.json#ST-1", "statement.json#ST-2"]


def test_provider_wrong_payee_and_amount_are_both_reported():
    provider = _provider(amount_paise=4_700_000, metadata={"vendor_id": "V-0005"})

    result = three_way(_decision(), _payment(), provider, None, None, _policy())

    assert result.status == SettlementStatus.CONFLICT
    assert set(result.flags) == {"AMOUNT_MISMATCH", "WRONG_PAYEE_SUSPECTED"}


def test_amount_date_candidate_is_only_a_possible_match():
    statement = [_line(ref=None, day=date(2026, 11, 9))]
    statement[0] = statement[0].model_copy(update={"bank_reference": None})

    result = three_way(_decision(), _payment(), _provider(), statement, None, _policy())

    assert result.status == SettlementStatus.POSSIBLE_MATCH
    assert result.statement_refs == ["statement.json#ST-1"]
    assert result.flags == []


def test_missing_statement_is_not_available_and_nonterminal_provider_is_pending():
    missing = three_way(_decision(), _payment(), _provider(), None, None, _policy())
    pending = three_way(_decision(), _payment(status=PaymentStatus.SUBMITTED),
                        _provider(status=ProviderStatus.PROCESSING), None, None, _policy())

    assert missing.status == SettlementStatus.NOT_AVAILABLE
    assert pending.status == SettlementStatus.PENDING


def test_unmatched_statement_and_returned_external_history_are_distinguished():
    unmatched = three_way(_decision(), _payment(), _provider(),
                          [_line(ref="other-ref", amount=3_000_000)], None, _policy())
    returned_record = _history(status="RETURNED", provider_ref="hist-ref", bank_reference="hist-bank")
    returned = three_way(None, None, None, [_line(ref="hist-bank")], [returned_record], _policy())

    assert unmatched.status == SettlementStatus.UNMATCHED
    assert returned.status == SettlementStatus.CONFLICT
    assert returned.flags == ["RETURNED_PAYMENT"]
    assert returned.provider_ref == "hist-ref"


def test_duplicate_successful_history_rows_raise_duplicate_payment():
    result = three_way(_decision(), None, None, None,
                       [_history(ref="HIST-1"), _history(ref="HIST-2", provider_ref="pout-2")], _policy())

    assert result.status == SettlementStatus.CONFLICT
    assert result.flags == ["DUPLICATE_PAYMENT"]
    assert result.differences["duplicate_payment_refs"] == ["HIST-1", "HIST-2"]


def test_wrong_history_amount_is_not_silently_filtered_out():
    result = three_way(_decision(), None, None, None, [_history(amount=4_700_000)], _policy())

    assert result.status == SettlementStatus.CONFLICT
    assert result.flags == ["AMOUNT_MISMATCH"]
