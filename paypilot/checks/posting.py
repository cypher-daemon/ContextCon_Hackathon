"""Posting-scope checks for direction, currency, and positive amounts (C2)."""
from __future__ import annotations

from paypilot.context import StageContext
from paypilot.schemas import BankLine, CheckResult, Invoice, VendorMessage, WorkItem


def check_posting_scope(item: WorkItem, ctx: StageContext) -> CheckResult:
    """Reject prohibited posting directions/currencies and non-positive values."""
    if ctx.operation not in ("POST_BANK_LINES", "PAY_INVOICES") or isinstance(item, VendorMessage):
        return CheckResult(check_id="posting", status="SKIPPED", reasons=["Posting scope does not apply."],
                           details={"operation": ctx.operation.value if ctx.operation else None})

    flags: list[str] = []
    reasons: list[str] = []
    details: dict[str, object] = {"operation": ctx.operation.value}
    allowed_currencies = set(ctx.policy.posting.allowed_currencies)

    if item.currency not in allowed_currencies:
        flags.append("UNSUPPORTED_CURRENCY")
        reasons.append(f"Currency {item.currency!r} is not allowed for posting.")
        details["currency_allowed"] = False
    else:
        details["currency_allowed"] = True

    if isinstance(item, BankLine):
        allowed_directions = set(ctx.policy.posting.allowed_directions)
        if item.direction not in allowed_directions:
            flags.append("CREDIT_LINE")
            reasons.append(f"Bank-line direction {item.direction!r} is not allowed for posting.")
            details["direction_allowed"] = False
        else:
            details["direction_allowed"] = True
        amount = item.amount_paise
    elif isinstance(item, Invoice):
        amount = item.amount_paise
    else:
        raise TypeError(f"unsupported work item type: {type(item).__name__}")

    if amount is not None and amount <= 0:
        flags.append("NON_POSITIVE_AMOUNT")
        reasons.append("Amount must be greater than zero.")
        details["amount_positive"] = False
    else:
        details["amount_positive"] = amount is not None

    status = "FAIL" if flags else "UNKNOWN" if amount is None else "PASS"
    return CheckResult(check_id="posting", status=status, flags=flags,
                       reasons=reasons, missing=["amount_paise"] if amount is None else [], details=details)
