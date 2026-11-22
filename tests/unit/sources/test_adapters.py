from types import SimpleNamespace

from paypilot.schemas import ExtResponse
from paypilot.sources.ifsc import lookup
from paypilot.sources.crustdata import identify_by_domain
from paypilot.sources.webfetch import fetch_identity_claim
from tests.fakes import FakeExtClient, make_ctx


def source_policy():
    return SimpleNamespace(sources=SimpleNamespace(
        ifsc=SimpleNamespace(enabled=True, base_url="https://ifsc.test"),
        crustdata=SimpleNamespace(enabled=True, base_url="https://crust.test", identify_by="domain"),
        webfetch=SimpleNamespace(enabled=True, paths=["/", "/about"], skip_domains=["blocked.test"],
                                 cf_markers=["Just a moment", "cf-chl"], passage_window=60)),
        normalize=SimpleNamespace(), match=SimpleNamespace(), decision_hash_input="test")


def test_ifsc_results_and_source_limits():
    bad = FakeExtClient({"ifsc.test": FakeExtClient.err("NOT_FOUND", 404)})
    assert lookup(make_ctx(policy=source_policy(), ext=bad), "ABCD0123456", "i").ifsc_valid == "FAIL"
    good = FakeExtClient({"ifsc.test": FakeExtClient.ok({"IFSC": "ABCD0123456", "BANK": "Example", "BRANCH": "Main"})})
    result = lookup(make_ctx(policy=source_policy(), ext=good), "ABCD0123456", "i")
    assert result.ifsc_valid == "PASS" and result.evidence[0].record_date is None
    assert "ownership" in result.evidence[0].limitations[0].lower()


def test_crustdata_domain_only_and_tie():
    payload = [{"matches": [
        {"confidence_score": 1, "company_data": {"crustdata_company_id": "1", "basic_info": {"name": "Brand A"}}},
        {"confidence_score": 1, "company_data": {"crustdata_company_id": "2", "basic_info": {"name": "Brand B"}}}]}]
    class Ext:
        def post_json(self, provider, operation, url, body, **kwargs):
            assert body == {"domains": ["example.test"]}
            return ExtResponse(ok=True, status=200, data=payload, retrieved_at=make_ctx().clock())
    result = identify_by_domain(make_ctx(policy=source_policy(), ext=Ext()), "EXAMPLE.TEST", "i")
    assert result.status == "TIE" and len(result.evidence) == 2


def test_webfetch_cites_cin_and_skips_challenge_or_blocked_host():
    html = "<footer>Delhivery Limited CIN: L63090DL2011PLC221234</footer>"
    ext = FakeExtClient({"delhivery.test": FakeExtClient.ok(html)})
    result = fetch_identity_claim(make_ctx(policy=source_policy(), ext=ext), "delhivery.test", "i")
    assert result.cin in result.evidence.field_or_passage
    assert result.evidence.provider == "company_website_claim"
    assert result.evidence.record_date is None
    assert fetch_identity_claim(make_ctx(policy=source_policy(), ext=ext), "sub.blocked.test", "i") is None
    challenge = FakeExtClient({"delhivery.test": FakeExtClient.ok("Just a moment cf-chl")})
    assert fetch_identity_claim(make_ctx(policy=source_policy(), ext=challenge), "delhivery.test", "i") is None
