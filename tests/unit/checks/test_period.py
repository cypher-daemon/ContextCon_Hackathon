from datetime import date

from paypilot.checks.period import check_period
from paypilot.context import StageContext
from paypilot.core.config import load_config
from paypilot.schemas import BankLine, Operation, Period
from tests.fakes import make_ctx


def _line(day: date) -> BankLine:
    return BankLine(item_id="i1", source_ref="bank_lines.json#BL-1", line_id="BL-1", date=day,
                    descriptor_raw="RAZ*SWIGGY BLR", descriptor_key="SWIGGY", amount_paise=100,
                    currency="INR", direction="DEBIT", transaction_fingerprint="fingerprint")


def _ctx(period: Period | None) -> StageContext:
    _, policy, _ = load_config("northbay")
    return make_ctx(policy=policy, operation=Operation.POST_BANK_LINES, period=period)


def test_period_includes_both_endpoints() -> None:
    period = Period(from_="2026-10-01", to="2026-10-31")

    assert check_period(_line(date(2026, 10, 1)), _ctx(period)).status == "PASS"
    assert check_period(_line(date(2026, 10, 31)), _ctx(period)).status == "PASS"


def test_out_of_period_is_flagged() -> None:
    result = check_period(_line(date(2026, 11, 2)), _ctx(Period(from_="2026-10-01", to="2026-10-31")))

    assert result.status == "FAIL"
    assert result.flags == ["OUT_OF_PERIOD"]


def test_missing_request_period_is_skipped() -> None:
    result = check_period(_line(date(2026, 10, 5)), _ctx(None))

    assert result.status == "SKIPPED"
    assert result.flags == []
