"""Conservative default exception mappings from FAILURE_MODES.md §2.

Client policy mappings override these defaults; PERSISTENCE_FAILURE is purposely
absent because it is logged outside the database and never classified here.
"""
from __future__ import annotations

from paypilot.policy_config import ExceptionMapping
from paypilot.schemas import Severity


def _mapping(type_: str, severity: Severity, *, post: Severity | None = None,
             blocking: bool = True, recovery: bool = False) -> ExceptionMapping:
    return ExceptionMapping(type=type_, severity_pre=severity, severity_post=post or severity,
                            blocking=blocking, recovery_case=recovery)


DEFAULT_EXCEPTION_MAP = {
    "MISSING_FIELD": _mapping("INPUT_INCOMPLETE", Severity.MEDIUM),
    "PARSE_FAILED": _mapping("INPUT_INCOMPLETE", Severity.MEDIUM),
    "UNSUPPORTED_REQUEST": _mapping("INPUT_UNSUPPORTED", Severity.LOW),
    "OUT_OF_PERIOD": _mapping("INPUT_OUT_OF_SCOPE", Severity.LOW),
    "CREDIT_LINE": _mapping("INPUT_OUT_OF_SCOPE", Severity.MEDIUM),
    "UNSUPPORTED_CURRENCY": _mapping("INPUT_OUT_OF_SCOPE", Severity.MEDIUM),
    "NON_POSITIVE_AMOUNT": _mapping("INPUT_OUT_OF_SCOPE", Severity.MEDIUM),
    "PAYEE_UNRESOLVED": _mapping("IDENTITY_UNRESOLVED", Severity.MEDIUM),
    "PAYEE_TIE": _mapping("IDENTITY_AMBIGUOUS", Severity.HIGH),
    "LOOKALIKE": _mapping("IDENTITY_AMBIGUOUS", Severity.HIGH),
    "FUZZY_MATCH": _mapping("IDENTITY_AMBIGUOUS", Severity.MEDIUM),
    "PARENT_BRAND": _mapping("LEGAL_ENTITY_MISMATCH", Severity.HIGH),
    "WEBSITE_IDENTITY_CLAIM_ONLY": _mapping("IDENTITY_UNVERIFIED", Severity.MEDIUM),
    "VENDOR_ONBOARDING_REQUIRED": _mapping("IDENTITY_UNRESOLVED", Severity.MEDIUM),
    "VENDOR_MASTER_CONFLICT": _mapping("VENDOR_MASTER_CONFLICT", Severity.HIGH),
    "DUPLICATE_INVOICE": _mapping("DUPLICATE_INVOICE", Severity.HIGH),
    "DUPLICATE_TRANSACTION": _mapping("DUPLICATE_TRANSACTION", Severity.HIGH),
    "ALREADY_PAID": _mapping("ALREADY_PAID", Severity.HIGH),
    "DUPLICATE_PAYMENT": _mapping("DUPLICATE_PAYMENT", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "DUPLICATE_DEBIT": _mapping("DUPLICATE_DEBIT", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "PO_MISSING": _mapping("PO_MISMATCH", Severity.MEDIUM),
    "PO_NOT_FOUND": _mapping("PO_MISMATCH", Severity.MEDIUM),
    "PO_VENDOR_MISMATCH": _mapping("PO_MISMATCH", Severity.MEDIUM),
    "PO_AMOUNT_MISMATCH": _mapping("PO_MISMATCH", Severity.MEDIUM),
    "PO_QTY_MISMATCH": _mapping("PO_MISMATCH", Severity.MEDIUM),
    "PO_EXHAUSTED": _mapping("PO_MISMATCH", Severity.MEDIUM),
    "ACCOUNT_UNMAPPED": _mapping("ACCOUNT_UNMAPPED", Severity.MEDIUM),
    "BANK_CHANGED": _mapping("BANK_CHANGE_REQUEST", Severity.HIGH),
    "BANK_CHANGE_REQUEST": _mapping("BANK_CHANGE_REQUEST", Severity.HIGH),
    "IFSC_INVALID": _mapping("IFSC_INVALID", Severity.HIGH),
    "SOURCE_UNAVAILABLE": _mapping("SOURCE_UNAVAILABLE", Severity.MEDIUM),
    "CREDITS_EXHAUSTED": _mapping("SOURCE_UNAVAILABLE", Severity.MEDIUM),
    "COST_LIMIT": _mapping("RUN_LIMIT_REACHED", Severity.MEDIUM),
    "TIME_LIMIT": _mapping("RUN_LIMIT_REACHED", Severity.MEDIUM),
    "PROVIDER_TIMEOUT": _mapping("PROVIDER_OUTCOME_UNKNOWN", Severity.HIGH),
    "PAYMENT_UNCERTAIN": _mapping("PROVIDER_OUTCOME_UNKNOWN", Severity.HIGH),
    "WRONG_PAYEE_SUSPECTED": _mapping("WRONG_PAYEE", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "AMOUNT_MISMATCH": _mapping("AMOUNT_MISMATCH", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "SETTLEMENT_MISMATCH": _mapping("SETTLEMENT_MISMATCH", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "RETURNED_PAYMENT": _mapping("PAYMENT_RETURNED", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "REVERSED_PAYMENT": _mapping("PAYMENT_RETURNED", Severity.HIGH, post=Severity.CRITICAL, recovery=True),
    "STALE_APPROVAL": _mapping("STALE_APPROVAL", Severity.HIGH),
    "VENDOR_CONTAINED": _mapping("VENDOR_CONTAINED", Severity.HIGH),
}
