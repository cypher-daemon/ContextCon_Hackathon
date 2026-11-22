"""Contract: the shared fakes behave (unscripted external call is never a PASS; unknown price stays unknown)."""
import pytest

from paypilot.context import BudgetExceeded
from tests.fakes import FakeBudget, FakeExtClient, make_ctx


def test_unscripted_ext_call_is_not_ok():
    r = FakeExtClient().get_json("ifsc", "lookup", "https://ifsc.example/XXXX", item_id="i", stage="s")
    assert r.ok is False and r.error == "NOT_FOUND"


def test_budget_unknown_price_reserves_and_stops():
    b = FakeBudget(limit_inr=1.5, unknown_reserve_inr=1.0)
    b.reserve(None)
    with pytest.raises(BudgetExceeded):
        b.reserve(None)


def test_make_ctx():
    ctx = make_ctx()
    assert ctx.run_id == "R-test" and ctx.clock().tzinfo is not None
