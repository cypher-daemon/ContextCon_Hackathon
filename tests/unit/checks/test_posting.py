from datetime import date

from paypilot.checks.posting import check_posting_scope
from paypilot.core.config import load_config
from paypilot.schemas import BankLine, Operation
from tests.fakes import make_ctx


def _line(*, direction="DEBIT", currency="INR", amount_paise=100) -> BankLine:
    return BankLine(item_id="i1", source_ref="bank_lines.json#BL-1", line_id="BL-1", date=date(2026, 10, 5),
                    descriptor_raw="RAZ*SWIGGY BLR", descriptor_key="SWIGGY", amount_paise=amount_paise,
                    currency=currency, direction=direction, transaction_fingerprint="fingerprint")


def _ctx():
    _, policy, _ = load_config("northbay")
    return make_ctx(policy=policy, operation=Operation.POST_BANK_LINES)


def test_credit_line_is_rejected_by_posting_policy() -> None:
    result = check_posting_scope(_line(direction="CREDIT"), _ctx())

    assert result.status == "FAIL"
    assert result.flags == ["CREDIT_LINE"]


def test_unsupported_currency_and_non_positive_amount_are_flagged() -> None:
    result = check_posting_scope(_line(currency="USD", amount_paise=0), _ctx())

    assert result.status == "FAIL"
    assert result.flags == ["UNSUPPORTED_CURRENCY", "NON_POSITIVE_AMOUNT"]


def test_valid_debit_passes_posting_scope() -> None:
    result = check_posting_scope(_line(), _ctx())

    assert result.status == "PASS"
    assert result.flags == []
