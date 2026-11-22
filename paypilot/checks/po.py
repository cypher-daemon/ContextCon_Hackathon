"""Purchase-order validation for invoice candidates (C4b)."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from paypilot.context import StageContext
from paypilot.core.ids import source_id
from paypilot.schemas import (CheckResult, DateBasis, Invoice, PurchaseOrder, Resolution, Source,
                              VendorMessage, WorkItem)

_RESOLVED = {"RESOLVED_VENDOR_MASTER", "RESOLVED_EXACT_IDENTIFIER"}
_CLOSED_PO_STATES = {"CLOSED", "CANCELLED", "CANCELED", "COMPLETE", "COMPLETED", "EXHAUSTED", "FULLY_BILLED"}


def check_po(item: WorkItem, resolution: Resolution, ctx: StageContext) -> CheckResult:
    """Check vendor, amount, quantity, and prior billed amount for an invoice PO."""
    if not isinstance(item, Invoice):
        return CheckResult(check_id="po", status="SKIPPED", reasons=["PO validation applies only to invoices."],
                           details={"kind": type(item).__name__})
    if ctx.operation not in ctx.policy.po.required_for and not item.po_number:
        return CheckResult(check_id="po", status="SKIPPED", reasons=["No PO is required for this operation."],
                           details={"operation": ctx.operation.value if ctx.operation else None})
    if not ctx.data.pos:
        return CheckResult(check_id="po", status="SKIPPED", reasons=["Client has no purchase-order data."],
                           details={"po_number": item.po_number, "reason": "NO_PO_DATA"})
    if not item.po_number:
        if ctx.operation in ctx.policy.po.required_for:
            return CheckResult(check_id="po", status="FAIL", flags=["PO_MISSING"],
                               reasons=["A purchase order is required for this operation."],
                               details={"po_number": None, "required": True})
        return CheckResult(check_id="po", status="SKIPPED", reasons=["Invoice has no PO and none is required."],
                           details={"po_number": None, "required": False})
    if resolution.status not in _RESOLVED or not resolution.vendor_id:
        return CheckResult(check_id="po", status="SKIPPED",
                           reasons=["Cannot validate PO ownership without a resolved vendor."],
                           details={"po_number": item.po_number, "vendor_id": None})

    po = next((row for row in ctx.data.pos if row.po_number == item.po_number), None)
    if po is None:
        return CheckResult(check_id="po", status="FAIL", flags=["PO_NOT_FOUND"],
                           reasons=[f"Purchase order {item.po_number} was not found."],
                           details={"po_number": item.po_number, "vendor_id": resolution.vendor_id})

    flags: list[str] = []
    reasons: list[str] = []
    billed = ctx.history.po_billed_paise(item.po_number, before_item=item.item_id)
    remaining = max(po.amount_paise - billed, 0)
    tolerance = _amount_tolerance(po.amount_paise, ctx.policy.po.tolerance_pct,
                                  ctx.policy.po.tolerance_abs_paise)
    if po.vendor_id != resolution.vendor_id:
        flags.append("PO_VENDOR_MISMATCH")
        reasons.append(f"PO {po.po_number} belongs to vendor {po.vendor_id}, not {resolution.vendor_id}.")

    status = po.status.strip().upper()
    if status in _CLOSED_PO_STATES or (billed > 0 and item.amount_paise is not None and
                                       billed + item.amount_paise > po.amount_paise + tolerance):
        flags.append("PO_EXHAUSTED")
        reasons.append(f"PO {po.po_number} has only {remaining} paise remaining before this invoice.")

    # A PO is an authorization ceiling; a smaller invoice is a valid partial
    # bill. Compare the invoice against the remaining authorized amount.
    if item.amount_paise is not None and item.amount_paise - remaining > tolerance:
        difference = item.amount_paise - remaining
        pct = (Decimal(difference) * Decimal(100) / Decimal(po.amount_paise)) if po.amount_paise else Decimal(0)
        flags.append("PO_AMOUNT_MISMATCH")
        reasons.append(f"Invoice differs from PO by {difference} paise ({pct:+.1f}%), beyond tolerance.")
    if po.qty is not None and item.qty is not None:
        qty_tolerance = Decimal(str(po.qty)) * Decimal(str(ctx.policy.po.qty_tolerance_pct)) / Decimal(100)
        qty_difference = Decimal(str(item.qty)) - Decimal(str(po.qty))
        if qty_difference > qty_tolerance:
            flags.append("PO_QTY_MISMATCH")
            reasons.append(f"Invoice quantity differs from PO by {qty_difference:+g}, beyond tolerance.")

    evidence = [_po_source(po, "po_number", po.po_number, ctx),
                _po_source(po, "vendor_id", po.vendor_id, ctx),
                _po_source(po, "amount_paise", str(po.amount_paise), ctx)]
    if po.qty is not None:
        evidence.append(_po_source(po, "qty", str(po.qty), ctx))
    details = {"po_number": po.po_number, "po_vendor_id": po.vendor_id,
               "invoice_vendor_id": resolution.vendor_id, "po_amount_paise": po.amount_paise,
               "invoice_amount_paise": item.amount_paise, "billed_before_paise": billed,
               "remaining_before_paise": remaining, "amount_tolerance_paise": tolerance,
               "po_qty": po.qty, "invoice_qty": item.qty, "po_status": po.status}
    return CheckResult(check_id="po", status="FAIL" if flags else "PASS", flags=flags,
                       reasons=reasons, details=details, evidence=evidence)


def _amount_tolerance(amount_paise: int, pct: float, abs_paise: int) -> int:
    percent = (Decimal(amount_paise) * Decimal(str(pct)) / Decimal(100)).to_integral_value(rounding=ROUND_CEILING)
    return max(int(percent), abs_paise)


def _po_source(po: PurchaseOrder, field: str, value: str, ctx: StageContext) -> Source:
    return Source(source_id=source_id("purchase_orders", po.record_ref, f"po.{field}", field),
                  provider="purchase_orders", record_ref=po.record_ref, fact=f"po.{field}", value=value,
                  field_or_passage=field, record_date=po.record_date,
                  record_date_basis=DateBasis.RECORD_FIELD if po.record_date else DateBasis.UNKNOWN,
                  retrieved_at=ctx.clock(), limitations=[] if po.record_date else ["PO has no record date"])
