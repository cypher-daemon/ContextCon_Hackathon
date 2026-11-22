from datetime import date

import pytest

from paypilot.checks.runner import run_checks
from paypilot.core.config import load_config
from paypilot.policy_config import CheckName
from paypilot.schemas import BankLine, Operation, Period, Resolution
from tests.fakes import make_ctx


def _line(line_id: str, day: date, direction: str) -> BankLine:
    return BankLine(item_id=line_id, source_ref=f"bank_lines.json#{line_id}", line_id=line_id, date=day,
                    descriptor_raw="RAZ*SWIGGY BLR", descriptor_key="SWIGGY", amount_paise=100,
                    currency="INR", direction=direction, transaction_fingerprint=line_id)


def _ctx(*, c2_only: bool = True):
    _, policy, _ = load_config("northbay")
    if c2_only:
        policy = policy.model_copy(update={"checks_order": [CheckName.REQUIRED, CheckName.PERIOD]})
    return make_ctx(policy=policy, operation=Operation.POST_BANK_LINES,
                    period=Period(from_="2026-10-01", to="2026-10-31"))


def _resolution() -> Resolution:
    return Resolution(status="RESOLVED_VENDOR_MASTER", vendor_id="V-0001", legal_name="Swiggy Limited")


def test_runner_flags_sc02_out_of_period_and_credit_cases() -> None:
    outside = run_checks(_line("BL-102", date(2026, 11, 2), "DEBIT"), _resolution(), _ctx())
    credit = run_checks(_line("BL-103", date(2026, 10, 15), "CREDIT"), _resolution(), _ctx())

    assert [check.check_id for check in outside] == ["required", "period", "posting"]
    assert "OUT_OF_PERIOD" in [flag for check in outside for flag in check.flags]
    assert "CREDIT_LINE" in [flag for check in credit for flag in check.flags]


def test_runner_fails_closed_when_a_configured_later_check_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="configured check 'duplicate'"):
        run_checks(_line("BL-101", date(2026, 10, 14), "DEBIT"), _resolution(), _ctx(c2_only=False))
