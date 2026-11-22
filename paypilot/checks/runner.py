"""C2 check orchestration; future configured checks fail closed until implemented."""
from __future__ import annotations

from paypilot.checks.period import check_period
from paypilot.checks.posting import check_posting_scope
from paypilot.checks.required import check_required
from paypilot.context import StageContext
from paypilot.schemas import BankLine, CheckResult, Invoice, Resolution, VendorMessage

def run_checks(item: BankLine | Invoice | VendorMessage, resolution: Resolution, ctx: StageContext) -> list[CheckResult]:
    """Run checks in policy order; apply posting scope immediately after period.

    Duplicate, PO, account and bank handlers are added by their later C tasks.
    Reaching one before its implementation is present raises instead of
    silently allowing an item through without the configured check.
    """
    del resolution  # reserved for later identity-aware checks
    results: list[CheckResult] = []
    for check_name in ctx.policy.checks_order:
        if check_name == "required":
            results.append(check_required(item, ctx))
        elif check_name == "period":
            results.append(check_period(item, ctx))
            results.append(check_posting_scope(item, ctx))
        else:
            raise NotImplementedError(f"configured check {check_name.value!r} is not implemented yet")
    return results
