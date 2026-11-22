from datetime import date

from paypilot.checks.required import check_required
from paypilot.context import StageContext
from paypilot.core.config import load_config
from paypilot.schemas import Invoice, Operation
from tests.fakes import make_ctx


def _invoice(**overrides) -> Invoice:
    data = dict(item_id="i1", source_ref="invoices.json#INV-1", invoice_id="INV-1",
                vendor_name_raw="Delhivery Limited", invoice_no_raw="DLV-1", invoice_no_canonical="DLV-1",
                invoice_date=date(2026, 10, 3), amount_paise=1000, currency="INR",
                transaction_fingerprint="fingerprint")
    data.update(overrides)
    return Invoice(**data)


def _ctx() -> StageContext:
    _, policy, _ = load_config("northbay")
    return make_ctx(policy=policy, operation=Operation.PAY_INVOICES)


def test_required_fields_pass_when_present() -> None:
    result = check_required(_invoice(), _ctx())

    assert result.status == "PASS"
    assert result.flags == []
    assert result.missing == []


def test_required_fields_identify_all_missing_invoice_values() -> None:
    result = check_required(_invoice(vendor_name_raw="  ", invoice_no_raw=None, invoice_date=None, amount_paise=None), _ctx())

    assert result.status == "FAIL"
    assert result.flags == ["MISSING_FIELD"]
    assert result.missing == ["vendor_name_raw", "invoice_no_raw", "invoice_date", "amount_paise"]
