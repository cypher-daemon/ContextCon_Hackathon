"""Requested-period checks (C2)."""
from __future__ import annotations

from datetime import date

from paypilot.context import StageContext
from paypilot.schemas import BankLine, CheckResult, Invoice, VendorMessage, WorkItem


def check_period(item: WorkItem, ctx: StageContext) -> CheckResult:
    """Hold dated items outside the inclusive request period."""
    if not ctx.policy.period.enforce:
        return CheckResult(check_id="period", status="SKIPPED", reasons=["Period enforcement is disabled."],
                           details={"enforced": False})
    if ctx.period is None:
        return CheckResult(check_id="period", status="SKIPPED", reasons=["No request period was supplied."],
                           details={"enforced": True})

    item_date = _item_date(item)
    if item_date is None:
        # Missing invoice dates are handled by the required-fields check. Do not
        # report a period PASS when no date can be compared.
        return CheckResult(check_id="period", status="UNKNOWN", missing=["date"],
                           reasons=["Cannot check the period because the item has no date."],
                           details={"period_from": ctx.period.from_, "period_to": ctx.period.to})

    outside = item_date < ctx.period.from_ or item_date > ctx.period.to
    return CheckResult(
        check_id="period",
        status="FAIL" if outside else "PASS",
        flags=["OUT_OF_PERIOD"] if outside else [],
        reasons=[f"Item date {item_date.isoformat()} is outside the requested period."] if outside else [],
        details={"item_date": item_date, "period_from": ctx.period.from_, "period_to": ctx.period.to},
    )


def _item_date(item: WorkItem) -> date | None:
    if isinstance(item, BankLine):
        return item.date
    if isinstance(item, Invoice):
        return item.invoice_date
    if isinstance(item, VendorMessage):
        return item.received_at.date()
    raise TypeError(f"unsupported work item type: {type(item).__name__}")
