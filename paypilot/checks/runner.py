"""Orchestrate the implemented checks in the configured order."""
from __future__ import annotations

from paypilot.checks.account import check_account
from paypilot.checks.bank import check_bank
from paypilot.checks.duplicate import check_duplicate
from paypilot.checks.period import check_period
from paypilot.checks.po import check_po
from paypilot.checks.posting import check_posting_scope
from paypilot.checks.required import check_required
from paypilot.context import StageContext
from paypilot.schemas import BankLine, CheckResult, Invoice, Resolution, VendorMessage

def run_checks(item: BankLine | Invoice | VendorMessage, resolution: Resolution, ctx: StageContext) -> list[CheckResult]:
    """Run checks in policy order; apply posting scope immediately after period.

    Each handler returns SKIPPED for item kinds or operations where the check
    does not apply; an unknown configured check still fails closed.
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
            results.append(check_po(item, resolution, ctx))
        elif check_name == "bank":
            results.append(check_bank(item, resolution, ctx))
        else:
            raise NotImplementedError(f"configured check {check_name.value!r} is not implemented yet")
    return results
