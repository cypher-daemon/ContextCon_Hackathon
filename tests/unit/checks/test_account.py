from datetime import date

from paypilot.checks.account import check_account
from paypilot.core.config import load_config
from paypilot.schemas import ClientData, Invoice, Resolution, Vendor
from tests.fakes import make_ctx


def _vendor(**overrides) -> Vendor:
    data = dict(vendor_id="V-0001", legal_name="Swiggy Limited", name_key="SWIGGY", aliases=["SWIGGY"],
                default_account="Staff meals", category="food", record_date=date(2026, 9, 30),
                record_ref="clients/northbay/vendor_master.csv#V-0001")
    data.update(overrides)
    return Vendor(**data)


def _item() -> Invoice:
    return Invoice(item_id="INV-1", source_ref="invoices.json#INV-1", invoice_id="INV-1",
                   vendor_name_raw="Swiggy Limited", invoice_no_raw="S-1", invoice_date=date(2026, 10, 1),
                   amount_paise=100, transaction_fingerprint="fp")


def _ctx(vendor: Vendor, *, accounts=None, chart=None):
    _, policy, _ = load_config("northbay")
    if accounts is not None:
        policy = policy.model_copy(update={"accounts": policy.accounts.model_copy(update={"by_category": accounts})})
    data = ClientData(client_id="northbay", vendors=[vendor], accounts=chart)
    return make_ctx(policy=policy, data=data)


def _resolved(vendor_id="V-0001") -> Resolution:
    return Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id=vendor_id, legal_name="Swiggy Limited")


def test_vendor_default_account_wins_and_is_cited() -> None:
    result = check_account(_item(), _resolved(), _ctx(_vendor()))

    assert result.status == "PASS"
    assert result.details["account"] == "Staff meals"
    assert result.evidence[0].provider == "vendor_master"
    assert result.evidence[0].field_or_passage == "default_account"
    assert result.evidence[0].record_date == date(2026, 9, 30)


def test_policy_category_mapping_is_used_after_vendor_default() -> None:
    result = check_account(_item(), _resolved(), _ctx(_vendor(default_account=None), accounts={"food": "Meals"}))

    assert result.status == "PASS"
    assert result.details["account"] == "Meals"
    assert {source.provider for source in result.evidence} == {"vendor_master", "client_policy"}


def test_chart_category_mapping_is_used_when_policy_has_no_mapping() -> None:
    result = check_account(_item(), _resolved(), _ctx(_vendor(default_account=None), accounts={}, chart={"food": "Food"}))

    assert result.status == "PASS"
    assert result.details["account"] == "Food"
    assert "chart_of_accounts" in {source.provider for source in result.evidence}


def test_unmapped_category_is_flagged() -> None:
    result = check_account(_item(), _resolved(), _ctx(_vendor(default_account=None, category="unknown"), accounts={}))

    assert result.status == "FAIL"
    assert result.flags == ["ACCOUNT_UNMAPPED"]


def test_unresolved_candidate_is_not_used_for_account_selection() -> None:
    lookalike = Resolution(status="LOOKALIKE", vendor_id="V-0001", legal_name="Swiggy Limited")

    result = check_account(_item(), lookalike, _ctx(_vendor()))

    assert result.status == "SKIPPED"
    assert result.details["vendor_id"] is None
    assert result.evidence == []


def test_resolved_vendor_missing_from_client_data_fails_closed() -> None:
    result = check_account(_item(), _resolved("V-MISSING"), _ctx(_vendor()))

    assert result.status == "ERROR"
    assert result.flags == ["VENDOR_MASTER_CONFLICT"]
