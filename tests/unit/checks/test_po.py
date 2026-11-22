from datetime import date

from paypilot.checks.po import check_po
from paypilot.core.config import load_config
from paypilot.schemas import (ClientData, Invoice, Operation, PurchaseOrder, Resolution, Vendor)
from tests.fakes import FakeHistory, make_ctx


def _policy():
    return load_config("northbay")[1]


def _vendor(vendor_id="V-0005"):
    return Vendor(vendor_id=vendor_id, legal_name="VRL Logistics Limited", name_key="VRL LOGISTICS",
                  record_ref=f"vendor_master.csv#{vendor_id}")


def _po(**overrides):
    data = dict(po_number="PO-NB-1043", vendor_id="V-0005", amount_paise=6_000_000, qty=10,
                status="OPEN", record_date=date(2026, 10, 1), record_ref="purchase_orders.csv#PO-NB-1043")
    data.update(overrides)
    return PurchaseOrder(**data)


def _invoice(**overrides):
    data = dict(item_id="INV-203", source_ref="invoices.json#INV-203", invoice_id="INV-203",
                vendor_name_raw="VRL Logistics Limited", invoice_no_raw="VRL/11/2026/0332",
                invoice_date=date(2026, 11, 3), amount_paise=6_480_000, currency="INR",
                po_number="PO-NB-1043", qty=10, transaction_fingerprint="inv-fp")
    data.update(overrides)
    return Invoice(**data)


def _ctx(*, pos=None, history=None, operation=Operation.PAY_INVOICES):
    data = ClientData(client_id="northbay", vendors=[_vendor()], pos=pos)
    return make_ctx(policy=_policy(), data=data, history=history or FakeHistory(), operation=operation)


def _resolved(vendor_id="V-0005"):
    return Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id=vendor_id, legal_name="VRL Logistics Limited")


def test_po_check_skips_when_client_has_no_po_file():
    result = check_po(_invoice(), _resolved(), _ctx())

    assert result.status == "SKIPPED"
    assert result.details["reason"] == "NO_PO_DATA"


def test_required_missing_po_is_flagged():
    result = check_po(_invoice(po_number=None), _resolved(), _ctx(pos=[_po()]))

    assert result.flags == ["PO_MISSING"]


def test_po_not_found_and_vendor_mismatch_are_distinct_flags():
    missing = check_po(_invoice(po_number="PO-UNKNOWN"), _resolved(), _ctx(pos=[_po()]))
    wrong_vendor = check_po(_invoice(amount_paise=6_000_000), _resolved("V-0004"), _ctx(pos=[_po()]))

    assert missing.flags == ["PO_NOT_FOUND"]
    assert wrong_vendor.flags == ["PO_VENDOR_MISMATCH"]


def test_amount_tolerance_uses_greater_of_percent_or_absolute():
    within = check_po(_invoice(amount_paise=6_040_000), _resolved(), _ctx(pos=[_po()]))
    outside = check_po(_invoice(), _resolved(), _ctx(pos=[_po()]))

    assert within.status == "PASS"
    assert within.details["amount_tolerance_paise"] == 60_000
    assert outside.flags == ["PO_AMOUNT_MISMATCH"]
    assert "+8.0%" in outside.reasons[0]
    assert outside.evidence[0].provider == "purchase_orders"


def test_quantity_mismatch_and_previously_exhausted_po_are_flagged():
    qty_result = check_po(_invoice(amount_paise=6_000_000, qty=12), _resolved(), _ctx(pos=[_po()]))
    exhausted = check_po(_invoice(amount_paise=2_000_000), _resolved(),
                         _ctx(pos=[_po()], history=FakeHistory(po_billed={"PO-NB-1043": 5_000_000})))

    assert qty_result.flags == ["PO_QTY_MISMATCH"]
    assert exhausted.flags == ["PO_EXHAUSTED", "PO_AMOUNT_MISMATCH"]
