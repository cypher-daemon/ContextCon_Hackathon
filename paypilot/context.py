"""Ports and stage context (INTERFACES §8). Owner: lane A.

Lanes B and C reach the outside world ONLY through these ports: no sqlite3, no httpx (except sources/http.py), no files.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime
from typing import TYPE_CHECKING, Callable, Protocol

from paypilot.schemas import (CachedResponse, ClientData, ExtResponse, LedgerEntry, Operation, Payment, Period,
                              PriorRecord, ProviderPayment, RunConfig)

if TYPE_CHECKING:  # owned by lane C; imported lazily to keep the contract importable before C0 lands
    from paypilot.policy_config import PolicyConfig


class BudgetExceeded(Exception):
    """Cost limit reached; raised by BudgetPort.reserve."""


class TimeExceeded(Exception):
    """Runtime limit reached; raised by BudgetPort.check_time."""


class ProviderStatusUnknown(Exception):
    """Provider returned a status we do not recognise -> PAYMENT_UNCERTAIN, never mapped to success."""


class NotSupported(Exception):
    """Operation not supported by this provider (e.g. RazorpayX refund)."""


class LedgerPort(Protocol):  # A
    def reserve(self, e: LedgerEntry) -> str: ...
    def confirm(self, call_key: str, units: float, actual_inr: float | None, http_status: int | None, duration_ms: int) -> None: ...
    def fail(self, call_key: str, http_status: int | None, error: str) -> None: ...
    def cache_hit(self, e: LedgerEntry) -> None: ...


class CachePort(Protocol):  # A
    def get(self, provider: str, operation: str, request_hash: str) -> CachedResponse | None: ...
    def put(self, provider: str, operation: str, request_hash: str, resp: CachedResponse, ttl_h: int | None) -> None: ...


class BudgetPort(Protocol):  # A; raises BudgetExceeded / TimeExceeded
    def reserve(self, estimated_inr: float | None) -> None: ...
    def check_time(self) -> None: ...


class ExtClient(Protocol):  # B implements in sources/http.py over the Ledger/Cache/Budget ports
    def get_json(self, provider: str, operation: str, url: str, *, params: dict | None = None, item_id: str | None, stage: str) -> ExtResponse: ...
    def post_json(self, provider: str, operation: str, url: str, body: dict, *, item_id: str | None, stage: str) -> ExtResponse: ...
    def fetch_page(self, url: str, *, item_id: str | None, stage: str) -> ExtResponse: ...


class HistoryView(Protocol):  # A implements over SQLite + payment_history; read-only
    # before_item order = (date, input_name, row_no): the first occurrence proceeds, later ones are held.
    def prior_by_invoice(self, vendor_id: str, invoice_no_canonical: str, *, before_item: str | None) -> list[PriorRecord]: ...
    def prior_by_txn(self, key: str, amount_paise: int, currency: str, date_from: Date, date_to: Date, *, before_item: str | None) -> list[PriorRecord]: ...
    def prior_by_fingerprint(self, fingerprint: str, *, before_item: str | None) -> list[PriorRecord]: ...
    def payments_for_invoice(self, vendor_id: str, invoice_no_canonical: str) -> list[Payment]: ...
    def po_billed_paise(self, po_number: str, *, before_item: str | None) -> int: ...
    def open_critical_for_vendor(self, vendor_id: str) -> list[str]: ...  # containment


class PaymentProvider(Protocol):  # A: payments/fake.py (default), payments/razorpayx_test.py
    def create(self, key: str, amount_paise: int, currency: str, metadata: dict) -> ProviderPayment: ...
    def confirm(self, provider_ref: str, key: str) -> ProviderPayment: ...
    def retrieve(self, provider_ref: str) -> ProviderPayment: ...
    def find_by_attempt_key(self, attempt_key: str) -> ProviderPayment | None: ...
    def refund(self, provider_ref: str, key: str) -> ProviderPayment: ...


@dataclass(frozen=True)
class StageContext:
    run_id: str
    session_id: str
    client_id: str
    operation: Operation | None
    period: Period | None
    cfg: RunConfig
    policy: "PolicyConfig"          # carries decision_hash_input (policy_hash + data_hash)
    data: ClientData
    ext: ExtClient
    history: HistoryView
    clock: Callable[[], datetime]
