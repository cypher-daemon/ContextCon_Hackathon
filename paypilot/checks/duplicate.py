"""Duplicate invoice and bank-transaction checks (C3)."""
from __future__ import annotations

from datetime import timedelta

from paypilot.context import StageContext
from paypilot.core.ids import source_id
from paypilot.resolve.normalize import invoice_canonical
from paypilot.schemas import (BankLine, CheckResult, DateBasis, Invoice, PriorRecord, Resolution, Source,
                              VendorMessage, WorkItem)

_RESOLVED = {"RESOLVED_VENDOR_MASTER", "RESOLVED_EXACT_IDENTIFIER"}
_PAID_STATES = {"PAID", "PROVIDER_CONFIRMED", "SUCCEEDED", "PROCESSED", "SETTLED"}


def check_duplicate(item: WorkItem, resolution: Resolution, ctx: StageContext) -> CheckResult:
    """Find prior canonical invoices or same bank transactions without mutating history."""
    if isinstance(item, Invoice):
        return _check_invoice(item, resolution, ctx)
    if isinstance(item, BankLine):
        return _check_bank_line(item, resolution, ctx)
    if isinstance(item, VendorMessage):
        return CheckResult(check_id="duplicate", status="SKIPPED",
                           reasons=["Duplicate matching does not apply to vendor messages."],
                           details={"kind": "VendorMessage"})
    raise TypeError(f"unsupported work item type: {type(item).__name__}")


def _check_invoice(item: Invoice, resolution: Resolution, ctx: StageContext) -> CheckResult:
    canonical = invoice_canonical(item.invoice_no_raw or "", ctx.policy.normalize)
    if not canonical:
        return CheckResult(check_id="duplicate", status="SKIPPED",
                           reasons=["Cannot compare invoices without an invoice number."],
                           missing=["invoice_no_raw"], details={"invoice_no_canonical": None})
    if resolution.status not in _RESOLVED or not resolution.vendor_id:
        return CheckResult(check_id="duplicate", status="SKIPPED",
                           reasons=["Cannot compare invoice history without a resolved vendor."],
                           details={"invoice_no_canonical": canonical, "vendor_id": None})

    enabled_sources = {source.value for source in ctx.policy.duplicate.compare_against}
    priors = ctx.history.prior_by_invoice(resolution.vendor_id, canonical, before_item=item.item_id)
    matches = _eligible_priors(priors, enabled_sources)
    if not matches:
        return CheckResult(check_id="duplicate", status="PASS",
                           details={"invoice_no_canonical": canonical, "vendor_id": resolution.vendor_id})

    paid = [prior for prior in matches if (prior.state or "").upper() in _PAID_STATES]
    selected_flag = "ALREADY_PAID" if paid else "DUPLICATE_INVOICE"
    evidence = [_invoice_evidence(prior, canonical, ctx) for prior in matches]
    references = [prior.ref for prior in matches]
    reason = (f"Invoice {canonical} already appears as paid in prior record {references[0]}"
              if paid else f"Invoice {canonical} duplicates prior record {references[0]}")
    return CheckResult(
        check_id="duplicate", status="FAIL", flags=[selected_flag], reasons=[reason],
        details={"invoice_no_canonical": canonical, "vendor_id": resolution.vendor_id,
                 "duplicate_refs": references, "paid_refs": [prior.ref for prior in paid]},
        evidence=evidence,
    )


def _check_bank_line(item: BankLine, resolution: Resolution, ctx: StageContext) -> CheckResult:
    enabled_sources = {source.value for source in ctx.policy.duplicate.compare_against}
    matches: list[tuple[PriorRecord, str]] = []

    # A bank reference/UTR is a direct transaction identity. It can be checked
    # even when the payee is unresolved; it is never replaced with a name guess.
    if item.bank_reference and {"this_run", "db_history", "payment_history"} & enabled_sources:
        for prior in ctx.history.prior_by_fingerprint(item.transaction_fingerprint, before_item=item.item_id):
            if prior.source in enabled_sources:
                matches.append((prior, "fingerprint"))

    # Without a UTR, use the configured amount/date window and require the same
    # exactly resolved vendor, preventing descriptor collisions across payees.
    if (not item.bank_reference and resolution.status in _RESOLVED and resolution.vendor_id and
            {"this_run", "db_history"} & enabled_sources):
        window = ctx.policy.duplicate.window_days
        date_from = item.date - timedelta(days=window)
        date_to = item.date + timedelta(days=window)
        priors = ctx.history.prior_by_txn(item.descriptor_key, item.amount_paise, item.currency,
                                          date_from, date_to, before_item=item.item_id)
        matches.extend((prior, "window") for prior in priors
                       if prior.source in enabled_sources and prior.vendor_id == resolution.vendor_id)

    matches = _unique_matches(matches)
    if not matches:
        return CheckResult(
            check_id="duplicate", status="PASS",
            details={"vendor_id": resolution.vendor_id if resolution.status in _RESOLVED else None,
                     "window_days": ctx.policy.duplicate.window_days,
                     "match_method": "fingerprint" if item.bank_reference else "vendor_amount_date"},
        )

    evidence = [_bank_evidence(prior, item, method, ctx) for prior, method in matches]
    refs = [prior.ref for prior, _ in matches]
    methods = sorted({method for _, method in matches})
    return CheckResult(
        check_id="duplicate", status="FAIL", flags=["DUPLICATE_TRANSACTION"],
        reasons=[f"Possible duplicate bank transaction matches prior record {refs[0]}"],
        details={"duplicate_refs": refs, "match_methods": methods,
                 "vendor_id": resolution.vendor_id if resolution.status in _RESOLVED else None,
                 "amount_paise": item.amount_paise, "currency": item.currency,
                 "date": item.date, "window_days": ctx.policy.duplicate.window_days},
        evidence=evidence,
    )


def _eligible_priors(priors: list[PriorRecord], enabled_sources: set[str]) -> list[PriorRecord]:
    return sorted((prior for prior in priors if prior.source in enabled_sources),
                  key=lambda prior: (prior.date.isoformat() if prior.date else "", prior.ref))


def _unique_matches(matches: list[tuple[PriorRecord, str]]) -> list[tuple[PriorRecord, str]]:
    unique: dict[tuple[str, str, str], tuple[PriorRecord, str]] = {}
    for prior, method in matches:
        key = (prior.source, prior.item_id or "", prior.record_ref or prior.ref)
        unique.setdefault(key, (prior, method))
    return sorted(unique.values(), key=lambda pair: ((pair[0].date.isoformat() if pair[0].date else ""), pair[0].ref))


def _prior_source(prior: PriorRecord, fact: str, value: str, field: str, ctx: StageContext) -> Source:
    provider = "payment_history" if prior.source == "payment_history" else "batch_input"
    ref = prior.record_ref or prior.ref
    return Source(
        source_id=source_id(provider, ref, fact, field), provider=provider, record_ref=ref,
        fact=fact, value=value, field_or_passage=field, record_date=prior.date,
        record_date_basis=DateBasis.RECORD_FIELD if prior.date else DateBasis.UNKNOWN,
        retrieved_at=ctx.clock(),
        limitations=[] if prior.date else ["prior record has no date"],
    )


def _invoice_evidence(prior: PriorRecord, canonical: str, ctx: StageContext) -> Source:
    return _prior_source(prior, "duplicate.invoice_no_canonical", canonical, "invoice_no_canonical", ctx)


def _bank_evidence(prior: PriorRecord, current: BankLine, method: str, ctx: StageContext) -> Source:
    if method == "fingerprint":
        return _prior_source(prior, "duplicate.transaction_fingerprint", current.transaction_fingerprint,
                             "transaction_fingerprint", ctx)
    match_value = "|".join((current.descriptor_key, str(current.amount_paise), current.currency,
                            prior.date.isoformat() if prior.date else ""))
    return _prior_source(prior, "duplicate.transaction_window_match", match_value,
                         "descriptor_key,amount_paise,currency,date", ctx)
