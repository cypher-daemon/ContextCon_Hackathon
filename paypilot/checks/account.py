"""Expense-account assignment from the resolved vendor or category (C4a)."""
from __future__ import annotations

from paypilot.context import StageContext
from paypilot.core.ids import source_id
from paypilot.schemas import (BankLine, CheckResult, DateBasis, Invoice, Resolution, Source,
                              Vendor, VendorMessage, WorkItem)

_RESOLVED = {"RESOLVED_VENDOR_MASTER", "RESOLVED_EXACT_IDENTIFIER"}


def check_account(item: WorkItem, resolution: Resolution, ctx: StageContext) -> CheckResult:
    """Use vendor default account, then policy/chart category mapping."""
    if isinstance(item, VendorMessage):
        return CheckResult(check_id="account", status="SKIPPED",
                           reasons=["Account assignment does not apply to vendor messages."],
                           details={"kind": "VendorMessage"})
    if not isinstance(item, (BankLine, Invoice)):
        raise TypeError(f"unsupported work item type: {type(item).__name__}")
    if resolution.status not in _RESOLVED or not resolution.vendor_id:
        return CheckResult(check_id="account", status="SKIPPED",
                           reasons=["Cannot assign an account without a resolved vendor."],
                           details={"vendor_id": None})

    vendor = next((record for record in ctx.data.vendors if record.vendor_id == resolution.vendor_id), None)
    if vendor is None:
        return CheckResult(check_id="account", status="ERROR", flags=["VENDOR_MASTER_CONFLICT"],
                           reasons=[f"Resolved vendor {resolution.vendor_id} is absent from client data."],
                           details={"vendor_id": resolution.vendor_id})

    default_account = _non_blank(vendor.default_account)
    if default_account:
        source = _vendor_source(vendor, "default_account", default_account, ctx)
        return CheckResult(check_id="account", status="PASS", details={"account": default_account,
                           "source": "vendor_default", "vendor_id": vendor.vendor_id}, evidence=[source])

    category = _non_blank(vendor.category)
    if category:
        account = _non_blank(ctx.policy.accounts.by_category.get(category))
        if account:
            source = _policy_mapping_source(ctx, category, account)
            return CheckResult(check_id="account", status="PASS", details={"account": account,
                               "source": "policy_category", "category": category,
                               "vendor_id": vendor.vendor_id},
                               evidence=[_vendor_source(vendor, "category", category, ctx), source])

        account = _non_blank((ctx.data.accounts or {}).get(category))
        if account:
            source = _chart_mapping_source(ctx, category, account)
            return CheckResult(check_id="account", status="PASS", details={"account": account,
                               "source": "chart_category", "category": category,
                               "vendor_id": vendor.vendor_id},
                               evidence=[_vendor_source(vendor, "category", category, ctx), source])

    return CheckResult(
        check_id="account", status="FAIL", flags=["ACCOUNT_UNMAPPED"],
        reasons=[f"No default account or category mapping exists for vendor {vendor.vendor_id}."] if not category
                 else [f"No account mapping exists for vendor category {category!r}."],
        details={"vendor_id": vendor.vendor_id, "category": category, "account": None},
    )


def _non_blank(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()


def _vendor_source(vendor: Vendor, field: str, value: str, ctx: StageContext) -> Source:
    fact = f"vendor.{field}"
    return Source(
        source_id=source_id("vendor_master", vendor.record_ref, fact, field),
        provider="vendor_master", record_ref=vendor.record_ref, fact=fact, value=value,
        field_or_passage=field, record_date=vendor.record_date,
        record_date_basis=DateBasis.RECORD_FIELD if vendor.record_date else DateBasis.UNKNOWN,
        retrieved_at=ctx.clock(),
        limitations=[] if vendor.record_date else ["vendor master has no record date"],
    )


def _policy_mapping_source(ctx: StageContext, category: str, account: str) -> Source:
    ref = f"clients/{ctx.client_id}/policy.yaml#accounts.by_category.{category}"
    field = f"accounts.by_category.{category}"
    return Source(source_id=source_id("client_policy", ref, "account", field), provider="client_policy",
                  record_ref=ref, fact="account", value=account, field_or_passage=field,
                  record_date=None, record_date_basis=DateBasis.UNKNOWN, retrieved_at=ctx.clock(),
                  limitations=["client policy has no record date"])


def _chart_mapping_source(ctx: StageContext, category: str, account: str) -> Source:
    ref = f"clients/{ctx.client_id}/chart_of_accounts.csv#category:{category}"
    field = f"category={category}"
    return Source(source_id=source_id("chart_of_accounts", ref, "account", field), provider="chart_of_accounts",
                  record_ref=ref, fact="account", value=account, field_or_passage=field,
                  record_date=None, record_date_basis=DateBasis.UNKNOWN, retrieved_at=ctx.clock(),
                  limitations=["chart of accounts has no record date"])
