from datetime import date

from paypilot.checks.duplicate import check_duplicate
from paypilot.context import StageContext
from paypilot.core.config import load_config
from paypilot.resolve.normalize import invoice_canonical
from paypilot.schemas import (BankLine, Invoice, Operation, PriorRecord, Resolution, Vendor)
from tests.fakes import FakeHistory, make_ctx


def _policy():
    return load_config("northbay")[1]


def _vendor(vendor_id="V-0004") -> Vendor:
    return Vendor(vendor_id=vendor_id, legal_name="Blue Dart Express Limited", name_key="BLUE DART EXPRESS",
                  aliases=["BLUE DART"], record_date=date(2026, 9, 30), record_ref=f"vendor_master.csv#{vendor_id}")


def _resolution(vendor_id="V-0004") -> Resolution:
    return Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id=vendor_id, legal_name="Blue Dart Express Limited")


def _invoice(number="BDE-26-27-07781") -> Invoice:
    return Invoice(item_id="current", source_ref="invoices.json#INV-202", invoice_id="INV-202",
                   vendor_name_raw="Blue Dart Express Limited", invoice_no_raw=number,
                   invoice_no_canonical=invoice_canonical(number, _policy().normalize),
                   invoice_date=date(2026, 10, 28), amount_paise=2_250_000,
                   currency="INR", transaction_fingerprint="invoice-fingerprint")


def _ctx(history: FakeHistory, *, policy=None, vendors=None) -> StageContext:
    return make_ctx(policy=policy or _policy(), data=None if vendors is None else
                    _client_data(vendors), history=history, operation=Operation.PAY_INVOICES)


def _client_data(vendors):
    from paypilot.schemas import ClientData
    return ClientData(client_id="northbay", vendors=vendors)


def test_reformatted_invoice_is_a_duplicate_with_evidence() -> None:
    prior = PriorRecord(ref="HIST-0091", vendor_id="V-0004",
                        invoice_no_canonical=invoice_canonical("BDE/26-27/7781", _policy().normalize),
                        amount_paise=2_250_000, currency="INR", date=date(2026, 10, 28), state="OPEN",
                        source="payment_history", record_ref="clients/northbay/payment_history.csv#HIST-0091")
    history = FakeHistory(priors=[prior])
    before = list(history.priors)

    result = check_duplicate(_invoice(), _resolution(), _ctx(history))

    assert result.status == "FAIL"
    assert result.flags == ["DUPLICATE_INVOICE"]
    assert result.details["duplicate_refs"] == ["HIST-0091"]
    assert result.evidence[0].provider == "payment_history"
    assert result.evidence[0].record_date == date(2026, 10, 28)
    assert history.priors == before  # checks only read the history port


def test_paid_invoice_history_emits_already_paid() -> None:
    prior = PriorRecord(ref="HIST-0091", vendor_id="V-0004",
                        invoice_no_canonical=invoice_canonical("BDE/26-27/7781", _policy().normalize),
                        date=date(2026, 10, 28), state="PAID", source="payment_history",
                        record_ref="clients/northbay/payment_history.csv#HIST-0091")

    result = check_duplicate(_invoice(), _resolution(), _ctx(FakeHistory(priors=[prior])))

    assert result.flags == ["ALREADY_PAID"]
    assert result.details["paid_refs"] == ["HIST-0091"]


def test_bank_line_duplicate_matches_same_vendor_within_inclusive_window() -> None:
    current_date = date(2026, 10, 7)
    prior = PriorRecord(ref="AWS EMEA#BL-002", item_id="prior", vendor_id="V-0002", amount_paise=1_850_000,
                        currency="INR", date=date(2026, 10, 4), state="POSTED", source="this_run",
                        record_ref="bank_lines.json#BL-002")
    line = BankLine(item_id="BL-003", source_ref="bank_lines.json#BL-003", line_id="BL-003", date=current_date,
                    descriptor_raw="AWS EMEA", descriptor_key="AWS EMEA", amount_paise=1_850_000,
                    currency="INR", direction="DEBIT", transaction_fingerprint="fp-003")
    ctx = make_ctx(policy=_policy(), history=FakeHistory(priors=[prior]), operation=Operation.POST_BANK_LINES)

    result = check_duplicate(line, _resolution("V-0002"), ctx)

    assert result.flags == ["DUPLICATE_TRANSACTION"]
    assert result.details["duplicate_refs"] == ["AWS EMEA#BL-002"]
    assert result.evidence[0].provider == "batch_input"


def test_bank_line_outside_window_or_for_another_vendor_is_not_a_match() -> None:
    prior = PriorRecord(ref="SWIGGY#BL-OLD", vendor_id="V-0001", amount_paise=1_850_000,
                        currency="INR", date=date(2026, 10, 3), source="db_history")
    line = BankLine(item_id="BL-003", source_ref="bank_lines.json#BL-003", line_id="BL-003",
                    date=date(2026, 10, 7), descriptor_raw="AWS EMEA", descriptor_key="AWS EMEA",
                    amount_paise=1_850_000, currency="INR", direction="DEBIT",
                    transaction_fingerprint="fp-003")
    ctx = make_ctx(policy=_policy(), history=FakeHistory(priors=[prior]), operation=Operation.POST_BANK_LINES)

    result = check_duplicate(line, _resolution("V-0002"), ctx)

    assert result.status == "PASS"
    assert result.flags == []


def test_utr_match_does_not_require_vendor_resolution() -> None:
    fingerprint = "same-utr-fingerprint"
    prior = PriorRecord(ref=fingerprint, item_id="prior", vendor_id="V-0002", amount_paise=1_850_000,
                        currency="INR", date=date(2025, 1, 1), source="db_history",
                        record_ref="bank_lines.json#BL-OLD")
    line = BankLine(item_id="BL-003", source_ref="bank_lines.json#BL-003", line_id="BL-003",
                    date=date(2026, 10, 7), descriptor_raw="Unknown", descriptor_key="UNKNOWN",
                    amount_paise=1_850_000, currency="INR", direction="DEBIT", bank_reference="UTR-1",
                    transaction_fingerprint=fingerprint)
    unresolved = Resolution(status="UNRESOLVED", method="none", flags=["PAYEE_UNRESOLVED"])
    ctx = make_ctx(policy=_policy(), history=FakeHistory(priors=[prior]), operation=Operation.POST_BANK_LINES)

    result = check_duplicate(line, unresolved, ctx)

    assert result.flags == ["DUPLICATE_TRANSACTION"]
    assert result.details["match_methods"] == ["fingerprint"]


def test_configured_comparison_sources_are_respected() -> None:
    prior = PriorRecord(ref="HIST-0091", vendor_id="V-0004",
                        invoice_no_canonical=invoice_canonical("BDE/26-27/7781", _policy().normalize),
                        date=date(2026, 10, 28), state="PAID", source="payment_history")
    policy = _policy().model_copy(update={"duplicate": _policy().duplicate.model_copy(update={"compare_against": []})})

    result = check_duplicate(_invoice(), _resolution(), _ctx(FakeHistory(priors=[prior]), policy=policy))

    assert result.status == "PASS"
    assert result.flags == []
