from types import SimpleNamespace

import pytest

from paypilot.resolve.normalize import descriptor_key, invoice_canonical, name_key


POLICY = SimpleNamespace(
    processor_prefixes=[r"^RAZ\*", r"^PAYTM\*", r"^UPI[-/ ]"],
    city_tokens=["BLR", "BENGALURU"],
    legal_suffixes=["PVT", "PRIVATE", "LTD", "LIMITED", "SARL"],
    abbreviations={"TRD": "TRADERS"},
    invoice_noise_tokens=["INV", "NO"],
)


@pytest.mark.parametrize(("raw", "expected"), [
    ("RAZ*SWIGGY BLR", "SWIGGY"),
    ("PAYTM*SHARMA TRD", "SHARMA TRADERS"),
    ("UPI/SWIGGY/BENGALURU", "SWIGGY"),
])
def test_descriptor_key(raw, expected):
    assert descriptor_key(raw, POLICY) == expected


def test_name_key_keeps_meaningful_entity_terms():
    assert name_key("Amazon Web Services EMEA SARL", POLICY) == "AMAZON WEB SERVICES EMEA"


def test_invoice_canonical_normalizes_separators_and_zero_padding():
    assert invoice_canonical("BDE/26-27/7781", POLICY) == "BDE|26|27|7781"
    assert invoice_canonical("BDE-26-27-07781", POLICY) == "BDE|26|27|7781"
