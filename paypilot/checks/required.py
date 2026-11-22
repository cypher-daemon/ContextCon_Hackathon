"""Required-input checks (C2)."""
from __future__ import annotations

from paypilot.context import StageContext
from paypilot.schemas import BankLine, CheckResult, Invoice, VendorMessage, WorkItem


def check_required(item: WorkItem, ctx: StageContext) -> CheckResult:
    """Report required fields missing for this item kind and operation."""
    kind = type(item).__name__
    fields_by_operation = ctx.policy.required_fields.model_dump().get(kind, {})
    required = fields_by_operation.get(ctx.operation.value if ctx.operation else "", [])
    values = _item_values(item)
    missing = [name for name in required if _is_missing(values.get(name))]
    return CheckResult(
        check_id="required",
        status="FAIL" if missing else "PASS",
        flags=["MISSING_FIELD"] if missing else [],
        reasons=[f"Required field is missing: {name}" for name in missing],
        missing=missing,
        details={"kind": kind, "required": required},
    )


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _item_values(item: WorkItem) -> dict[str, object]:
    if isinstance(item, BankLine):
        return {"date": item.date, "descriptor_raw": item.descriptor_raw, "amount_paise": item.amount_paise,
                "direction": item.direction}
    if isinstance(item, Invoice):
        return {"vendor_name_raw": item.vendor_name_raw, "invoice_no_raw": item.invoice_no_raw,
                "invoice_date": item.invoice_date, "amount_paise": item.amount_paise}
    if isinstance(item, VendorMessage):
        return {"message_id": item.message_id, "received_at": item.received_at,
                "vendor_name_raw": item.vendor_name_raw}
    raise TypeError(f"unsupported work item type: {type(item).__name__}")
