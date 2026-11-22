from datetime import date
from types import SimpleNamespace

import pytest

from paypilot.schemas import ClientData, Invoice, Vendor
from paypilot.resolve.matcher import resolve_vendor
from tests.fakes import make_ctx


NORM = SimpleNamespace(processor_prefixes=[r"^RAZ\*", r"^PAYTM\*"], city_tokens=["BLR"],
                       legal_suffixes=["PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "SARL"],
                       abbreviations={"TRD": "TRADERS"}, invoice_noise_tokens=[])
MATCH = SimpleNamespace(fuzzy_min_score=92, fuzzy_min_margin=5, lookalike_floor=75, aliases_extra={})


def vendor(vid, name, aliases=(), *, gstin=None, cin=None, domain=None, fingerprint=None):
    return Vendor(vendor_id=vid, legal_name=name, name_key=name.upper(), aliases=list(aliases), gstin=gstin, cin=cin,
                  domain=domain, account_fingerprint=fingerprint, record_date=date(2026, 9, 30),
                  record_ref=f"clients/northbay/vendor_master.csv#{vid}")


def invoice(name, **kwargs):
    return Invoice(item_id="i", source_ref="batch#i", invoice_id="i", vendor_name_raw=name,
                   transaction_fingerprint="tx", **kwargs)


def ctx(vendors):
    return make_ctx(data=ClientData(client_id="northbay", vendors=vendors),
                    policy=SimpleNamespace(normalize=NORM, match=MATCH, decision_hash_input="test"))


def test_exact_identifiers_precede_name_matching_and_are_sourced():
    v = vendor("V1", "Right Name Ltd", gstin="GST123")
    result = resolve_vendor(invoice("wrong name", gstin="GST123"), ctx([v]))
    assert (result.status, result.method, result.vendor_id) == ("RESOLVED_EXACT_IDENTIFIER", "gstin", "V1")
    assert result.evidence[0].record_ref.endswith("#V1")
    assert result.evidence[0].record_date == date(2026, 9, 30)


@pytest.mark.parametrize(("name", "vendors", "status", "vendor_id"), [
    ("Swiggy Limited", [vendor("V1", "Swiggy Limited", ["SWIGGY"])], "RESOLVED_VENDOR_MASTER", "V1"),
    ("AWS EMEA", [vendor("V2", "Amazon Web Services EMEA SARL", ["AWS EMEA"])], "RESOLVED_VENDOR_MASTER", "V2"),
    ("PAYTM*SHARMA TRD", [], "UNRESOLVED", None),
    ("OM SAI ROADWAYS", [vendor("V1", "Om Sai Roadways Private Limited", ["OM SAI ROADWAYS"]),
                         vendor("V2", "Om Sai Roadways LLP", ["OM SAI ROADWAYS"])], "TIE", None),
    ("Delhivery Logistics Pvt", [vendor("V1", "Delhivery Limited", ["DELHIVERY"])], "LOOKALIKE", "V1"),
])
def test_required_identity_cases(name, vendors, status, vendor_id):
    result = resolve_vendor(invoice(name), ctx(vendors))
    assert result.status == status
    assert result.vendor_id == vendor_id


def test_account_fingerprint_resolves_exactly():
    v = vendor("V1", "Vendor Ltd", fingerprint="fp-1")
    assert resolve_vendor(invoice("unrelated", account_fingerprint="fp-1"), ctx([v])).method == "account_fingerprint"
