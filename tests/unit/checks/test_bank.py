from datetime import date

from paypilot.checks.bank import check_bank
from paypilot.core.config import load_config
from paypilot.schemas import (ClientData, Invoice, Operation, Resolution, Vendor, VendorMessage)
from tests.fakes import FakeExtClient, FakeHistory, make_ctx


def _policy():
    return load_config("northbay")[1]


def _vendor(**overrides):
    data = dict(vendor_id="V-0003", legal_name="Delhivery Limited", name_key="DELHIVERY", aliases=["DELHIVERY"],
                account_fingerprint="master-fp", ifsc="IFSC-B", record_date=date(2026, 9, 30),
                record_ref="vendor_master.csv#V-0003")
    data.update(overrides)
    return Vendor(**data)


def _invoice(**overrides):
    data = dict(item_id="INV-201", source_ref="invoices.json#INV-201", invoice_id="INV-201",
                vendor_name_raw="Delhivery Limited", invoice_no_raw="DLV/2026-27/00418",
                invoice_date=date(2026, 11, 3), amount_paise=4_800_000, currency="INR",
                account_fingerprint="master-fp", ifsc="IFSC-B", transaction_fingerprint="inv-fp")
    data.update(overrides)
    return Invoice(**data)


def _ctx(*, vendor=None, history=None, ext=None, policy=None):
    policy = policy or _policy()
    data = ClientData(client_id="northbay", vendors=[vendor or _vendor()])
    return make_ctx(policy=policy, data=data, history=history or FakeHistory(), ext=ext or FakeExtClient({
        "IFSC-B": FakeExtClient.ok({"IFSC": "IFSC-B", "BANK": "Test Bank", "BRANCH": "Main"}),
    }), operation=Operation.PAY_INVOICES)


def _resolved():
    return Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id="V-0003", legal_name="Delhivery Limited")


def test_matching_remit_details_report_three_independent_bank_statuses():
    result = check_bank(_invoice(), _resolved(), _ctx())

    assert result.status == "PASS"
    assert result.flags == []
    assert result.details["ifsc_valid"] == "PASS"
    assert result.details["account_matches_master"] == "PASS"
    assert result.details["ownership_verified"] == "NOT_VERIFIED"
    assert "razorpay_ifsc" in {source.provider for source in result.evidence}


def test_changed_account_and_invalid_ifsc_are_both_flagged():
    ctx = _ctx(ext=FakeExtClient({"IFSC-INVALID": FakeExtClient.err("NOT_FOUND", 404)}))
    result = check_bank(_invoice(account_fingerprint="new-fp", ifsc="IFSC-INVALID"), _resolved(), ctx)

    assert result.flags == ["BANK_CHANGED", "IFSC_INVALID"]
    assert result.details["account_matches_master"] == "FAIL"
    assert result.details["ifsc_valid"] == "FAIL"
    assert "account fingerprint" in result.details["mismatch_fields"]
    assert "IFSC" in result.details["mismatch_fields"]


def test_changed_account_alone_is_blocked_without_echoing_account_values():
    result = check_bank(_invoice(account_fingerprint="new-fingerprint", ifsc=None), _resolved(), _ctx())

    assert result.flags == ["BANK_CHANGED"]
    assert result.details["mismatch_fields"] == ["account fingerprint"]
    assert "new-fingerprint" not in str(result.model_dump())
    assert "master-fp" not in str(result.model_dump())


def test_ifsc_source_failure_is_unknown_and_fails_closed():
    result = check_bank(_invoice(), _resolved(), _ctx(ext=FakeExtClient({"IFSC-B": FakeExtClient.err("TIMEOUT")})))

    assert result.details["ifsc_valid"] == "UNKNOWN"
    assert "SOURCE_UNAVAILABLE" in result.flags


def test_vendor_message_requests_change_and_checks_containment():
    message = VendorMessage(item_id="MSG-301", source_ref="vendor_messages.json#MSG-301", message_id="MSG-301",
                            received_at="2026-11-05T10:12:00+05:30", vendor_name_raw="Delhivery Limited",
                            from_domain="delhivery.com", kind="BANK_CHANGE", new_ifsc="IFSC-B")
    result = check_bank(message, _resolved(), _ctx(history=FakeHistory(contained={"V-0003": ["EXC-1"]})))

    assert result.flags == ["BANK_CHANGE_REQUEST", "VENDOR_CONTAINED"]
    assert result.details["ifsc_valid"] == "PASS"
    assert result.details["containment_refs"] == ["EXC-1"]
    assert result.details["ownership_verified"] == "NOT_VERIFIED"


def test_open_containment_blocks_a_new_invoice_even_when_bank_details_match():
    result = check_bank(_invoice(), _resolved(),
                        _ctx(history=FakeHistory(contained={"V-0003": ["EXC-2"]})))

    assert result.flags == ["VENDOR_CONTAINED"]
    assert result.details["containment_refs"] == ["EXC-2"]


def test_disabled_ifsc_lookup_reports_not_checked():
    policy = _policy().model_copy(update={"sources": _policy().sources.model_copy(update={
        "ifsc": _policy().sources.ifsc.model_copy(update={"enabled": False})})})
    result = check_bank(_invoice(ifsc="IFSC-B"), _resolved(), _ctx(policy=policy))

    assert result.details["ifsc_valid"] == "NOT_CHECKED"
