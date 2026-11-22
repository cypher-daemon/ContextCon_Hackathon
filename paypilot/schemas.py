"""Shared contract (INTERFACES §4). Owner: lane A. Additive changes only via docs/CONTRACT_CHANGES.md.

Money is integer paise. Time is timezone-aware. Models forbid extra fields.
"""
from __future__ import annotations

from datetime import date as Date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---- enums ----
class Operation(StrEnum):
    POST_BANK_LINES = "POST_BANK_LINES"
    PAY_INVOICES = "PAY_INVOICES"
    RECONCILE = "RECONCILE"


class ItemState(StrEnum):
    NEW = "NEW"; PARSED = "PARSED"; RESOLVED = "RESOLVED"; CHECKED = "CHECKED"; DECIDED = "DECIDED"
    POSTED = "POSTED"; AWAITING_APPROVAL = "AWAITING_APPROVAL"; APPROVED = "APPROVED"
    PAYMENT_IN_PROGRESS = "PAYMENT_IN_PROGRESS"; PAID = "PAID"; PAYMENT_UNCERTAIN = "PAYMENT_UNCERTAIN"
    FAILED = "FAILED"; HELD = "HELD"; CANNOT_VERIFY = "CANNOT_VERIFY"; REJECTED = "REJECTED"; STALE = "STALE"


class Outcome(StrEnum):
    POSTED = "POSTED"; AWAITING_APPROVAL = "AWAITING_APPROVAL"; PAID = "PAID"; HELD = "HELD"
    CANNOT_VERIFY = "CANNOT_VERIFY"; REJECTED = "REJECTED"; PAYMENT_UNCERTAIN = "PAYMENT_UNCERTAIN"
    FAILED = "FAILED"; RECONCILED = "RECONCILED"


class Proposal(StrEnum):  # what decide() may return
    CANNOT_VERIFY = "CANNOT_VERIFY"; HELD = "HELD"; AWAITING_APPROVAL = "AWAITING_APPROVAL"
    POSTED = "POSTED"; RECONCILED = "RECONCILED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"; COMPLETE = "COMPLETE"; PARTIAL = "PARTIAL"; STOPPED_COST = "STOPPED_COST"; STOPPED_TIME = "STOPPED_TIME"


class CheckStatus(StrEnum):
    PASS = "PASS"; FAIL = "FAIL"; SKIPPED = "SKIPPED"; ERROR = "ERROR"; UNKNOWN = "UNKNOWN"


class DateBasis(StrEnum):  # how record_date was established; never a retrieval time
    RECORD_FIELD = "RECORD_FIELD"; PUBLICATION_DATE = "PUBLICATION_DATE"; FILE_METADATA = "FILE_METADATA"; UNKNOWN = "UNKNOWN"


class PaymentStatus(StrEnum):
    CREATED = "CREATED"; SUBMITTED = "SUBMITTED"; PROVIDER_CONFIRMED = "PROVIDER_CONFIRMED"; FAILED = "FAILED"
    PAYMENT_UNCERTAIN = "PAYMENT_UNCERTAIN"; RETURNED = "RETURNED"; REVERSED = "REVERSED"


class AttemptStep(StrEnum):
    NONE = "NONE"; CREATE_SENT = "CREATE_SENT"; REF_SAVED = "REF_SAVED"; CONFIRM_SENT = "CONFIRM_SENT"; DONE = "DONE"


class SettlementStatus(StrEnum):
    NOT_AVAILABLE = "NOT_AVAILABLE"; PENDING = "PENDING"; MATCHED = "MATCHED"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"; CONFLICT = "CONFLICT"; UNMATCHED = "UNMATCHED"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"; HIGH = "HIGH"; MEDIUM = "MEDIUM"; LOW = "LOW"


class ExceptionStatus(StrEnum):
    DETECTED = "DETECTED"; CLASSIFIED = "CLASSIFIED"; CONTAINED = "CONTAINED"; INVESTIGATING = "INVESTIGATING"
    ACTION_REQUIRED = "ACTION_REQUIRED"; RESOLVED = "RESOLVED"; FALSE_POSITIVE = "FALSE_POSITIVE"
    ACCEPTED_RISK = "ACCEPTED_RISK"; CLOSED_NO_ACTION = "CLOSED_NO_ACTION"


class RecoveryStatus(StrEnum):
    OPEN = "OPEN"; ACTION_PREPARED = "ACTION_PREPARED"; HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
    EXTERNAL_RESPONSE_PENDING = "EXTERNAL_RESPONSE_PENDING"; RECOVERED = "RECOVERED"; UNRECOVERABLE = "UNRECOVERABLE"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"; FALSE_POSITIVE = "FALSE_POSITIVE"


class ProviderStatus(StrEnum):
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"; PROCESSING = "PROCESSING"; SUCCEEDED = "SUCCEEDED"
    DECLINED = "DECLINED"; CANCELED = "CANCELED"; RETURNED = "RETURNED"


# ---- request & inputs ----
class Limits(_M):
    max_runtime_seconds: int | None = None
    max_cost_inr: float | None = None


class Period(_M):
    from_: Date = Field(alias="from")
    to: Date


class Request(_M):
    schema_version: str
    request_id: str
    client_id: str
    operation: Operation | None = None
    instruction: str = ""
    period: Period | None = None
    inputs: dict[str, str] = {}
    limits: Limits | None = None
    source_path: str = ""
    source_sha: str = ""


class BankLine(_M):
    item_id: str; source_ref: str; line_id: str; date: Date
    descriptor_raw: str; descriptor_key: str
    amount_paise: int; currency: str
    direction: Literal["DEBIT", "CREDIT"]
    bank_reference: str | None = None
    transaction_fingerprint: str


class Invoice(_M):
    item_id: str; source_ref: str; invoice_id: str; vendor_name_raw: str
    gstin: str | None = None; domain: str | None = None
    invoice_no_raw: str | None = None; invoice_no_canonical: str | None = None
    invoice_date: Date | None = None; due_date: Date | None = None
    amount_paise: int | None = None; currency: str = "INR"
    po_number: str | None = None; qty: float | None = None
    account_fingerprint: str | None = None; account_last4: str | None = None
    ifsc: str | None = None; document_ref: str | None = None
    transaction_fingerprint: str


class VendorMessage(_M):
    item_id: str; source_ref: str; message_id: str; received_at: datetime
    from_domain: str | None = None; vendor_name_raw: str
    kind: Literal["BANK_CHANGE", "OTHER"]
    new_account_fingerprint: str | None = None; new_account_last4: str | None = None; new_ifsc: str | None = None


WorkItem = BankLine | Invoice | VendorMessage


class ParseFailure(_M):
    item_id: str; source_ref: str; errors: list[str]


# ---- client data ----
class Vendor(_M):
    vendor_id: str; legal_name: str; name_key: str; aliases: list[str] = []
    cin: str | None = None; gstin: str | None = None; domain: str | None = None
    account_fingerprint: str | None = None; account_last4: str | None = None; ifsc: str | None = None
    default_account: str | None = None; category: str | None = None
    record_date: Date | None = None; record_ref: str


class PurchaseOrder(_M):
    po_number: str; vendor_id: str; amount_paise: int; qty: float | None = None; status: str
    record_date: Date | None = None; record_ref: str


class HistoryRecord(_M):
    ref: str; vendor_id: str | None = None; invoice_no_canonical: str | None = None
    amount_paise: int; currency: str; paid_on: Date
    provider_ref: str | None = None; bank_reference: str | None = None
    status: str; record_ref: str


class ClientData(_M):
    client_id: str; vendors: list[Vendor]
    pos: list[PurchaseOrder] | None = None
    history: list[HistoryRecord] | None = None
    accounts: dict[str, str] | None = None            # category -> account name
    statement: list[BankLine] | None = None
    data_quality: list[str] = []                       # e.g. duplicate GSTIN in master


# ---- evidence ----
class Source(_M):
    # record_date = a date the source itself supplies; None + UNKNOWN if none. retrieved_at never goes into record_date.
    source_id: str; provider: str; record_ref: str; fact: str; value: str; field_or_passage: str
    record_date: Date | None = None; record_date_basis: DateBasis; retrieved_at: datetime
    limitations: list[str] = []


# ---- logic outputs (lanes B, C) ----
class Resolution(_M):
    status: Literal["RESOLVED_VENDOR_MASTER", "RESOLVED_EXACT_IDENTIFIER", "FUZZY_CANDIDATE", "LOOKALIKE", "TIE",
                    "PARENT_BRAND_MISMATCH", "WEBSITE_IDENTITY_CLAIM_FOUND", "UNRESOLVED", "SOURCE_UNAVAILABLE"]
    vendor_id: str | None = None; legal_name: str | None = None
    method: Literal["gstin", "cin", "account_fingerprint", "exact", "alias", "fuzzy", "website", "none"] = "none"
    score: float | None = None
    candidates: list[tuple[str, float]] = []
    flags: list[str] = []; reasons: list[str] = []
    evidence: list[Source] = []


class CheckResult(_M):
    check_id: str; status: CheckStatus
    flags: list[str] = []; reasons: list[str] = []; missing: list[str] = []
    details: dict[str, Any] = {}
    evidence: list[Source] = []


class Reason(_M):
    code: str; text: str; rule_id: str | None = None; evidence_refs: list[str] = []


class Facts(_M):
    operation: Operation; kind: str; vendor_id: str | None = None; account_fingerprint: str | None = None
    amount_paise: int | None = None; currency: str | None = None; date: Date | None = None
    invoice_no_canonical: str | None = None; account: str | None = None
    flags: list[str] = []                              # sorted


class Decision(_M):
    item_id: str; proposal: Proposal; flags: list[str] = []; reasons: list[Reason] = []; missing: list[str] = []
    evidence_refs: list[str] = []; rule_ids: list[str] = []; overridable: list[str] = []
    facts: Facts
    payee_legal_name: str | None = None; account: str | None = None
    bank_status: dict[str, str] | None = None
    decision_hash: str                                 # sha256(canonical_json(facts) | policy.decision_hash_input)


class ExceptionDraft(_M):
    type: str; severity: Severity; blocking: bool; summary: str; details: str; recommended_action: str
    evidence_refs: list[str] = []; related_refs: list[str] = []
    origin_key: str; item_id: str | None = None; payment_id: str | None = None
    open_recovery_case: bool = False


class ReconciliationResult(_M):
    subject_ref: str; status: SettlementStatus; instruction_ref: str | None = None
    provider_ref: str | None = None; statement_refs: list[str] = []
    differences: dict[str, Any] = {}; flags: list[str] = []; evidence: list[Source] = []


# ---- provider / external ports' value types ----
class ProviderPayment(_M):
    ref: str; status: ProviderStatus; amount_paise: int; currency: str
    metadata: dict[str, Any] = {}; raw_ref: str | None = None


class ExtResponse(_M):
    # Never raises for network/budget problems; error is one of
    # TIMEOUT | RATE_LIMITED | CREDITS_EXHAUSTED | AUTH | FORBIDDEN | NOT_FOUND | SERVER | BAD_BODY | BUDGET | BLOCKED
    ok: bool; status: int | None = None; data: Any = None; headers: dict[str, str] = {}
    error: str | None = None; cache_hit: bool = False
    retrieved_at: datetime; request_hash: str = ""


class CachedResponse(_M):
    status: int; headers: dict[str, str] = {}; body: str; retrieved_at: datetime


class PriorRecord(_M):
    ref: str; item_id: str | None = None; vendor_id: str | None = None; invoice_no_canonical: str | None = None
    amount_paise: int | None = None; currency: str | None = None; date: Date | None = None
    state: str | None = None; source: Literal["this_run", "db_history", "payment_history"] = "this_run"
    record_ref: str = ""


# ---- runtime records (lane A) ----
class Approval(_M):
    approval_id: str; item_id: str; decision_hash: str; approval_seq: int = 1
    status: Literal["PENDING", "APPROVED", "REJECTED", "SUPERSEDED"]
    reviewer: str | None = None; reason: str | None = None; overrides: list[str] = []
    requested_at: datetime; decided_at: datetime | None = None


class Payment(_M):
    payment_id: str; item_id: str; approval_id: str; payment_key: str; provider: str
    amount_paise: int; currency: str; status: PaymentStatus; settlement_status: SettlementStatus
    current_attempt: int = 1; created_at: datetime; updated_at: datetime


class PaymentAttempt(_M):
    attempt_key: str; payment_id: str; attempt_no: int; approval_id: str; provider_ref: str | None = None
    step: AttemptStep; outcome: Literal["PENDING", "SUCCEEDED", "DECLINED", "UNKNOWN"]
    last_error: str | None = None; created_at: datetime; updated_at: datetime


class ExceptionRecord(ExceptionDraft):
    exception_id: str; run_id: str; status: ExceptionStatus; assigned_to: str | None = None
    resolution_code: str | None = None; resolution_note: str | None = None; resolved_by: str | None = None
    resolved_at: datetime | None = None; created_at: datetime; updated_at: datetime


class RecoveryCase(_M):
    recovery_id: str; exception_id: str; payment_id: str; reason: str; requested_action: str
    status: RecoveryStatus; external_ref: str | None = None; evidence_refs: list[str] = []
    recovered_amount_paise: int | None = None


class JournalLine(_M):
    line_no: int; account: str; debit_paise: int = 0; credit_paise: int = 0


class JournalEntry(_M):
    entry_id: str; item_id: str
    kind: Literal["BANK_LINE_POSTING", "INVOICE_ACCRUAL", "INVOICE_PAYMENT", "REVERSAL"]
    vendor_id: str | None = None; decision_hash: str
    approval_id: str | None = None; attempt_key: str | None = None
    currency: str; memo: str; posted_at: datetime; lines: list[JournalLine]

    @model_validator(mode="after")
    def _balanced(self) -> "JournalEntry":
        if sum(l.debit_paise for l in self.lines) != sum(l.credit_paise for l in self.lines):
            raise ValueError("journal entry is not balanced (sum debits != sum credits)")
        return self


class AuditVerifyReport(_M):
    run_id: str; ok: bool; items_checked: int; decisions_replayed: int
    discrepancies: list[dict[str, Any]] = []; trial_balance: dict[str, int] = {}


class AuditEvent(_M):
    event_id: int; run_id: str; session_id: str; entity_type: str; entity_id: str
    previous_state: str | None = None; new_state: str | None = None; actor: str
    reason: str | None = None; metadata: dict[str, Any] = {}; ts: datetime


class LedgerEntry(_M):
    call_key: str; run_id: str; session_id: str; item_id: str | None = None; stage: str
    provider: str; operation: str
    unit_type: Literal["credits", "tokens_in", "tokens_out", "call"]
    units: float = 0.0; estimated_inr: float | None = None; actual_inr: float | None = None
    status: Literal["RESERVED", "CONFIRMED", "FAILED", "CACHE_HIT", "UNCONFIRMED"]
    http_status: int | None = None; duration_ms: int | None = None
    request_hash: str; attempt: int = 1; ts: datetime


class LedgerSummary(_M):
    run_id: str; total_inr: float | None = None
    cost_status: Literal["KNOWN", "PARTIAL", "UNKNOWN"]    # a null price is UNKNOWN, never 0
    unconfirmed_inr: float = 0.0; by_provider: dict[str, float | None] = {}
    calls: int = 0; cache_hits: int = 0; items: int = 0; finished_items: int = 0
    inr_per_item: float | None = None; worst_item_inr: float | None = None
    limit_inr: float = 0.0; zero_cost_items_pct: float = 0.0


class ItemResult(_M):
    run_id: str; item: WorkItem | ParseFailure; state: ItemState; outcome: Outcome | None = None
    decision: Decision | None = None; approval: Approval | None = None; payment: Payment | None = None
    attempts: list[PaymentAttempt] = []; journal: list[JournalEntry] = []
    exceptions: list[ExceptionRecord] = []; evidence: list[Source] = []
    cost_inr: float | None = None


class RunSummary(_M):
    run_id: str; request_id: str; client_id: str; operation: Operation | None = None; status: RunStatus
    counts: dict[str, int] = {}; open_exceptions: dict[str, int] = {}
    sessions: int = 0; recovered_from_crash: bool = False; elapsed_ms: int = 0
    cost: LedgerSummary


# ---- runtime config (lane A sections only; B/C sections live in policy_config.py, owner C) ----
class RunConfig(_M):
    """Merged, validated config for a run. `policy` sections are validated by paypilot.policy_config (C)."""
    limits: dict[str, Any]; payments: dict[str, Any]; approvals: dict[str, Any]; costs: dict[str, Any]
    output: dict[str, Any]; logging: dict[str, Any]; storage: dict[str, Any]
    config_hash: str = ""; policy_hash: str = ""
