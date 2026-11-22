"""Vendor bank-detail verification and containment checks (C5)."""
from __future__ import annotations

from typing import Any

from paypilot.context import StageContext
from paypilot.core.ids import source_id
from paypilot.schemas import (BankLine, CheckResult, DateBasis, Invoice, Resolution, Source,
                              Vendor, VendorMessage, WorkItem)
from paypilot.sources.ifsc import lookup as lookup_ifsc

_RESOLVED = {"RESOLVED_VENDOR_MASTER", "RESOLVED_EXACT_IDENTIFIER"}


def check_bank(item: WorkItem, resolution: Resolution, ctx: StageContext) -> CheckResult:
    """Compare invoice remit details with master and flag bank-change requests/containment."""
    if isinstance(item, BankLine):
        return CheckResult(check_id="bank", status="SKIPPED", reasons=["Bank-master verification does not apply to bank lines."],
                           details={"kind": "BankLine"})
    if not isinstance(item, (Invoice, VendorMessage)):
        raise TypeError(f"unsupported work item type: {type(item).__name__}")
    if resolution.status not in _RESOLVED or not resolution.vendor_id:
        if isinstance(item, VendorMessage) and item.kind == "BANK_CHANGE":
            return CheckResult(check_id="bank", status="FAIL", flags=["BANK_CHANGE_REQUEST"],
                               reasons=["Vendor message requests a bank-account change; vendor identity is unresolved."],
                               details={"ifsc_valid": "NOT_CHECKED", "account_matches_master": "UNKNOWN",
                                        "ownership_verified": "NOT_VERIFIED", "vendor_id": None})
        return CheckResult(check_id="bank", status="SKIPPED",
                           reasons=["Cannot compare bank details without a resolved vendor."],
                           details={"ifsc_valid": "NOT_CHECKED", "account_matches_master": "UNKNOWN",
                                    "ownership_verified": "NOT_VERIFIED", "vendor_id": None})

    vendor = next((record for record in ctx.data.vendors if record.vendor_id == resolution.vendor_id), None)
    if vendor is None:
        return CheckResult(check_id="bank", status="ERROR", flags=["VENDOR_MASTER_CONFLICT"],
                           reasons=[f"Resolved vendor {resolution.vendor_id} is absent from client data."],
                           details={"ifsc_valid": "NOT_CHECKED", "account_matches_master": "UNKNOWN",
                                    "ownership_verified": "NOT_VERIFIED", "vendor_id": resolution.vendor_id})

    flags: list[str] = []
    reasons: list[str] = []
    evidence: list[Source] = []
    details: dict[str, Any] = {"vendor_id": vendor.vendor_id,
                               "ifsc_valid": "NOT_CHECKED", "account_matches_master": "UNKNOWN",
                               "ownership_verified": "NOT_VERIFIED", "ifsc_bank": None, "ifsc_branch": None}
    if isinstance(item, Invoice):
        supplied_fingerprint = item.account_fingerprint
        supplied_ifsc = _clean(item.ifsc)
        master_fingerprint = vendor.account_fingerprint
        master_ifsc = _clean(vendor.ifsc)
        comparisons: list[bool] = []
        mismatch_fields: list[str] = []
        if supplied_fingerprint and master_fingerprint:
            same = supplied_fingerprint == master_fingerprint
            comparisons.append(same)
            mismatch_fields.extend([] if same else ["account fingerprint"])
            evidence.extend(_comparison_sources(item, vendor, "account_fingerprint", "MATCH" if same else "MISMATCH", ctx))
        if supplied_ifsc and master_ifsc:
            same = supplied_ifsc == master_ifsc
            comparisons.append(same)
            mismatch_fields.extend([] if same else ["IFSC"])
            evidence.extend(_comparison_sources(item, vendor, "ifsc", "MATCH" if same else "MISMATCH", ctx))
        if mismatch_fields:
            details["account_matches_master"] = "FAIL"
            details["mismatch_fields"] = mismatch_fields
            flags.append("BANK_CHANGED")
            reasons.append(f"Invoice bank details differ from the vendor master ({', '.join(mismatch_fields)}).")
        elif comparisons:
            details["account_matches_master"] = "PASS"
        if supplied_ifsc:
            _check_ifsc(item, supplied_ifsc, ctx, details, flags, reasons, evidence)
    else:
        supplied_fingerprint = item.new_account_fingerprint
        supplied_ifsc = _clean(item.new_ifsc)
        if item.kind == "BANK_CHANGE":
            flags.append("BANK_CHANGE_REQUEST")
            reasons.append("Vendor message requests a change to bank details; update requires independent verification.")
            evidence.append(_message_source(item, "bank_change_request", "BANK_CHANGE", ctx))
        if supplied_fingerprint and vendor.account_fingerprint:
            evidence.extend(_comparison_sources(item, vendor, "account_fingerprint", "PROPOSED_CHANGE", ctx))
        if supplied_ifsc:
            _check_ifsc(item, supplied_ifsc, ctx, details, flags, reasons, evidence)

    try:
        containment_refs = ctx.history.open_critical_for_vendor(vendor.vendor_id)
    except (AttributeError, NotImplementedError):
        containment_refs = []
    if containment_refs:
        flags.append("VENDOR_CONTAINED")
        reasons.append(f"Vendor {vendor.vendor_id} has open containment exception(s): {', '.join(sorted(containment_refs))}.")
        details["containment_refs"] = sorted(containment_refs)

    details["ownership_verified"] = "NOT_VERIFIED"
    details["ifsc"] = supplied_ifsc
    return CheckResult(check_id="bank", status="FAIL" if flags else "PASS", flags=list(dict.fromkeys(flags)),
                       reasons=reasons, details=details, evidence=evidence)


def _clean(value: str | None) -> str | None:
    return value.strip().upper() if value and value.strip() else None


def _check_ifsc(item: Invoice | VendorMessage, code: str, ctx: StageContext, details: dict[str, Any],
                flags: list[str], reasons: list[str], evidence: list[Source]) -> None:
    if not ctx.policy.bank.verify_master_ifsc_before_pay:
        details["ifsc_valid"] = "NOT_CHECKED"
        return
    result = lookup_ifsc(ctx, code, item.item_id)
    details["ifsc_valid"] = result.ifsc_valid
    details["ifsc_bank"] = result.bank
    details["ifsc_branch"] = result.branch
    evidence.extend(result.evidence)
    if result.ifsc_valid == "FAIL":
        flags.append("IFSC_INVALID")
        reasons.append(f"IFSC {code} was not found by the configured IFSC source.")
    elif result.ifsc_valid == "UNKNOWN":
        flags.append("SOURCE_UNAVAILABLE")
        reasons.append(result.reason or "IFSC source is unavailable; validity cannot be confirmed.")


def _comparison_sources(item: Invoice | VendorMessage, vendor: Vendor, field: str,
                        outcome: str, ctx: StageContext) -> list[Source]:
    input_ref = item.source_ref
    master_ref = vendor.record_ref
    fact = f"bank.{field}_comparison"
    return [
        Source(source_id=source_id("batch_input", input_ref, fact, field), provider="batch_input",
               record_ref=input_ref, fact=fact, value=outcome, field_or_passage=field,
               record_date=None, record_date_basis=DateBasis.UNKNOWN, retrieved_at=ctx.clock(),
               limitations=["Only comparison outcome is retained; account values are not exposed"]),
        Source(source_id=source_id("vendor_master", master_ref, fact, field), provider="vendor_master",
               record_ref=master_ref, fact=fact, value=outcome, field_or_passage=field,
               record_date=vendor.record_date,
               record_date_basis=DateBasis.RECORD_FIELD if vendor.record_date else DateBasis.UNKNOWN,
               retrieved_at=ctx.clock(), limitations=["Only comparison outcome is retained; account values are not exposed"]),
    ]


def _message_source(item: VendorMessage, fact: str, value: str, ctx: StageContext) -> Source:
    return Source(source_id=source_id("batch_input", item.source_ref, fact, "kind"), provider="batch_input",
                  record_ref=item.source_ref, fact=fact, value=value, field_or_passage="kind",
                  record_date=item.received_at.date(), record_date_basis=DateBasis.RECORD_FIELD,
                  retrieved_at=ctx.clock())
