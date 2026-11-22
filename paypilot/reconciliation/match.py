"""Pure three-way payment and bank-statement matching (C8)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from paypilot.schemas import (BankLine, Decision, HistoryRecord, Payment, PaymentStatus, ProviderPayment,
                              ProviderStatus, ReconciliationResult, SettlementStatus)

_SUCCESS_STATES = {"PAID", "SUCCEEDED", "PROVIDER_CONFIRMED", "SETTLED", "PROCESSED"}
_RETURNED_STATES = {"RETURNED", "RETURN", "PAYMENT_RETURNED"}
_REVERSED_STATES = {"REVERSED", "REVERSAL"}


def three_way(decision: Decision | None, payment: Payment | None, provider_view: ProviderPayment | None,
              statement: list[BankLine] | None, history: list[HistoryRecord] | None, policy: Any) -> ReconciliationResult:
    """Compare instruction, provider state, and statement without changing any input."""
    if decision and payment and decision.item_id != payment.item_id:
        raise ValueError("decision and payment refer to different items")

    records = list(history or [])
    facts = decision.facts if decision else None
    vendor_id = facts.vendor_id if facts else None
    canonical = facts.invoice_no_canonical if facts else None
    lines = list(statement or [])
    expected_amount = _first_not_none(
        payment.amount_paise if payment else None,
        facts.amount_paise if facts else None,
        provider_view.amount_paise if provider_view else None,
    )
    expected_currency = _first_not_none(
        payment.currency if payment else None,
        facts.currency if facts else None,
        provider_view.currency if provider_view else None,
    )
    matching_history = [record for record in records if vendor_id and canonical and
                        record.vendor_id == vendor_id and record.invoice_no_canonical == canonical]
    if not matching_history and records and not (vendor_id and canonical):
        refs = {provider_view.ref} if provider_view else set()
        refs.update(line.bank_reference for line in lines if line.bank_reference)
        matching_history = [record for record in records if refs.intersection(
            ref for ref in (record.provider_ref, record.bank_reference) if ref)]
        if not matching_history and lines:
            window = int(getattr(getattr(policy, "reconcile", None), "window_days", 0))
            matching_history = [record for record in records for line in lines
                                if line.direction == "DEBIT" and line.amount_paise == record.amount_paise
                                and line.currency == record.currency
                                and abs((line.date - record.paid_on).days) <= window]
    matching_history.sort(key=lambda record: (record.paid_on, record.ref))
    if expected_amount is None and len(matching_history) == 1:
        expected_amount = matching_history[0].amount_paise
    if expected_currency is None and len(matching_history) == 1:
        expected_currency = matching_history[0].currency

    flags: set[str] = set()
    differences: dict[str, Any] = {}
    provider_ref = provider_view.ref if provider_view else None
    if payment and provider_view and provider_view.amount_paise != payment.amount_paise:
        flags.add("AMOUNT_MISMATCH")
        differences["provider_amount_paise"] = {"expected": payment.amount_paise,
                                                "actual": provider_view.amount_paise}
    if payment and provider_view and provider_view.currency != payment.currency:
        flags.add("SETTLEMENT_MISMATCH")
        differences["provider_currency"] = {"expected": payment.currency, "actual": provider_view.currency}
    if facts and provider_view:
        actual_payees = _metadata_payees(provider_view.metadata)
        expected_payees = {facts.vendor_id.casefold()} if facts.vendor_id else set()
        if decision and decision.payee_legal_name:
            expected_payees.add(decision.payee_legal_name.casefold())
        wrong_payees = [value for value in actual_payees if value.casefold() not in expected_payees]
        if wrong_payees and expected_payees:
            flags.add("WRONG_PAYEE_SUSPECTED")
            differences["provider_payee"] = {"expected": sorted(expected_payees), "actual": wrong_payees}

    if payment:
        if payment.status == PaymentStatus.RETURNED:
            flags.add("RETURNED_PAYMENT")
        elif payment.status == PaymentStatus.REVERSED:
            flags.add("REVERSED_PAYMENT")
    if provider_view and provider_view.status == ProviderStatus.RETURNED:
        flags.add("RETURNED_PAYMENT")

    successful_history = [record for record in matching_history if record.status.strip().upper() in _SUCCESS_STATES]
    same_payment_history = [record for record in successful_history
                            if (expected_amount is None or record.amount_paise == expected_amount) and
                            (expected_currency is None or record.currency == expected_currency)]
    duplicate_history_groups: dict[tuple[int, str], list[HistoryRecord]] = {}
    for record in successful_history:
        duplicate_history_groups.setdefault((record.amount_paise, record.currency), []).append(record)
    duplicate_history = [record for group in duplicate_history_groups.values() if len(group) > 1 for record in group]
    if (payment and same_payment_history) or duplicate_history:
        flags.add("DUPLICATE_PAYMENT")
        differences["duplicate_payment_refs"] = [record.ref for record in
                                                   (same_payment_history if payment else duplicate_history)]
    for record in matching_history:
        if expected_amount is not None and record.amount_paise != expected_amount:
            flags.add("AMOUNT_MISMATCH")
            differences.setdefault("history_amount_paise", []).append(
                {"ref": record.ref, "expected": expected_amount, "actual": record.amount_paise})
        if expected_currency is not None and record.currency != expected_currency:
            flags.add("SETTLEMENT_MISMATCH")
            differences.setdefault("history_currency", []).append(
                {"ref": record.ref, "expected": expected_currency, "actual": record.currency})
    for record in matching_history:
        state = record.status.strip().upper()
        if state in _RETURNED_STATES:
            flags.add("RETURNED_PAYMENT")
            differences.setdefault("returned_refs", []).append(record.ref)
        elif state in _REVERSED_STATES:
            flags.add("REVERSED_PAYMENT")
            differences.setdefault("reversed_refs", []).append(record.ref)

    expected_refs = _expected_refs(provider_view, matching_history)
    direct = [line for line in lines if line.bank_reference and line.bank_reference in expected_refs]
    direct_debits = [line for line in direct if line.direction == "DEBIT"]
    if any(line.direction == "CREDIT" for line in direct):
        flags.add("REVERSED_PAYMENT")
        differences["credit_refs"] = [line.source_ref for line in direct if line.direction == "CREDIT"]
    if len(direct_debits) > 1:
        flags.add("DUPLICATE_DEBIT")
        differences["duplicate_debit_refs"] = [line.source_ref for line in direct_debits]

    expected_date = _expected_date(payment, matching_history, facts)
    window_days = int(getattr(getattr(policy, "reconcile", None), "window_days", 0))
    possible: list[BankLine] = []
    if not direct and expected_amount is not None and expected_currency:
        for line in lines:
            if line.direction != "DEBIT" or line.amount_paise != expected_amount or line.currency != expected_currency:
                continue
            if expected_date is not None and abs((line.date - expected_date).days) > window_days:
                continue
            possible.append(line)

    selected = direct_debits if direct_debits else possible
    if direct_debits:
        for line in direct_debits:
            if expected_amount is not None and line.amount_paise != expected_amount:
                flags.add("AMOUNT_MISMATCH")
                differences["statement_amount_paise"] = {"expected": expected_amount,
                                                         "actual": line.amount_paise}
            if expected_currency is not None and line.currency != expected_currency:
                flags.add("SETTLEMENT_MISMATCH")
                differences["statement_currency"] = {"expected": expected_currency,
                                                      "actual": line.currency}

    if not provider_ref and len(matching_history) == 1:
        provider_ref = matching_history[0].provider_ref
    statement_refs = [line.source_ref for line in selected]
    subject_ref = (payment.payment_id if payment else
                   decision.item_id if decision else
                   matching_history[0].ref if matching_history else
                   (lines[0].source_ref if lines else "unknown"))
    instruction_ref = payment.payment_key if payment else (decision.item_id if decision else None)

    conflict_flags = {"DUPLICATE_PAYMENT", "DUPLICATE_DEBIT", "WRONG_PAYEE_SUSPECTED", "AMOUNT_MISMATCH",
                      "SETTLEMENT_MISMATCH", "RETURNED_PAYMENT", "REVERSED_PAYMENT"}
    if flags.intersection(conflict_flags):
        status = SettlementStatus.CONFLICT
    elif direct_debits and len(direct_debits) == 1:
        status = SettlementStatus.MATCHED
    elif possible:
        status = SettlementStatus.POSSIBLE_MATCH
    elif _is_pending(payment, provider_view):
        status = SettlementStatus.PENDING
    elif statement is None:
        status = SettlementStatus.NOT_AVAILABLE
    else:
        status = SettlementStatus.UNMATCHED

    return ReconciliationResult(
        subject_ref=subject_ref, status=status, instruction_ref=instruction_ref,
        provider_ref=provider_ref, statement_refs=statement_refs,
        differences=differences, flags=sorted(flags), evidence=[],
    )


def _first_not_none(*values):
    return next((value for value in values if value is not None), None)


def _metadata_payees(metadata: dict[str, Any]) -> list[str]:
    keys = ("vendor_id", "payee_vendor_id", "vendorId", "payee", "vendor_name", "payee_name")
    return [str(metadata[key]).strip() for key in keys if metadata.get(key)]


def _expected_refs(provider_view: ProviderPayment | None, records: list[HistoryRecord]) -> set[str]:
    refs = {provider_view.ref} if provider_view else set()
    for record in records:
        if record.provider_ref:
            refs.add(record.provider_ref)
        if record.bank_reference:
            refs.add(record.bank_reference)
    return refs


def _expected_date(payment: Payment | None, records: list[HistoryRecord], facts: Any) -> date | None:
    if payment:
        return payment.updated_at.date()
    if records:
        return records[0].paid_on
    return facts.date if facts else None


def _is_pending(payment: Payment | None, provider_view: ProviderPayment | None) -> bool:
    if provider_view and provider_view.status in {ProviderStatus.PROCESSING, ProviderStatus.REQUIRES_CONFIRMATION}:
        return True
    return payment is not None and payment.status in {
        PaymentStatus.CREATED, PaymentStatus.SUBMITTED, PaymentStatus.PAYMENT_UNCERTAIN,
    }
