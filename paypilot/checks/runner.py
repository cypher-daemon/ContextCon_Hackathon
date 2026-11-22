"""Orchestrate the implemented checks in the configured order."""
from __future__ import annotations

from paypilot.checks.account import check_account
from paypilot.checks.duplicate import check_duplicate
from paypilot.checks.period import check_period
from paypilot.checks.posting import check_posting_scope
from paypilot.checks.required import check_required
from paypilot.context import StageContext
from paypilot.schemas import BankLine, CheckResult, Invoice, Resolution, VendorMessage

def run_checks(item: BankLine | Invoice | VendorMessage, resolution: Resolution, ctx: StageContext) -> list[CheckResult]:
    """Run checks in policy order; apply posting scope immediately after period.

    PO and bank handlers are added by later C tasks. They are explicitly
    skipped only for item kinds/operations where those checks do not apply;
    otherwise the runner raises instead of silently allowing an item through.
    """
    results: list[CheckResult] = []
    for check_name in ctx.policy.checks_order:
        if check_name == "required":
            results.append(check_required(item, ctx))
        elif check_name == "period":
            results.append(check_period(item, ctx))
            results.append(check_posting_scope(item, ctx))
        elif check_name == "duplicate":
            results.append(check_duplicate(item, resolution, ctx))
        elif check_name == "account":
            results.append(check_account(item, resolution, ctx))
        elif check_name == "po":
            if isinstance(item, Invoice) and ctx.operation == "PAY_INVOICES":
                raise NotImplementedError("configured check 'po' is not implemented yet")
            results.append(CheckResult(check_id="po", status="SKIPPED",
                                       reasons=["PO check does not apply to this operation."],
                                       details={"operation": ctx.operation.value if ctx.operation else None}))
        elif check_name == "bank":
            if isinstance(item, (Invoice, VendorMessage)) and ctx.operation == "PAY_INVOICES":
                raise NotImplementedError("configured check 'bank' is not implemented yet")
            results.append(CheckResult(check_id="bank", status="SKIPPED",
                                       reasons=["Bank-detail check does not apply to this operation."],
                                       details={"operation": ctx.operation.value if ctx.operation else None}))
        else:
            raise NotImplementedError(f"configured check {check_name.value!r} is not implemented yet")
    return results
