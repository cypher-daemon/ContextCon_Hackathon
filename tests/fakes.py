"""In-memory fakes for every port. Used by all lanes' unit tests (no network, no sqlite). Owner: lane A."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from paypilot.context import BudgetExceeded, StageContext, TimeExceeded
from paypilot.schemas import (CachedResponse, ClientData, ExtResponse, LedgerEntry, Operation, Payment, Period,
                              PriorRecord, RunConfig)

FIXED_NOW = datetime(2026, 11, 22, 8, 30, tzinfo=timezone.utc)


def fixed_clock() -> datetime:
    return FIXED_NOW


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class FakeExtClient:
    """Scripted responses keyed by URL substring. Unscripted calls return NOT_FOUND (never a PASS). Records every call."""

    def __init__(self, responses: dict[str, ExtResponse | Callable[..., ExtResponse]] | None = None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, str]] = []

    def _answer(self, provider: str, operation: str, url: str) -> ExtResponse:
        self.calls.append((provider, operation, url))
        for needle, r in self.responses.items():
            if needle in url:
                return r() if callable(r) else r
        return ExtResponse(ok=False, status=404, error="NOT_FOUND", retrieved_at=FIXED_NOW, request_hash=_hash(url))

    def get_json(self, provider, operation, url, *, params=None, item_id=None, stage=""):
        return self._answer(provider, operation, url)

    def post_json(self, provider, operation, url, body, *, item_id=None, stage=""):
        return self._answer(provider, operation, url)

    def fetch_page(self, url, *, item_id=None, stage=""):
        return self._answer("web", "fetch_page", url)

    @staticmethod
    def ok(data: Any, status: int = 200) -> ExtResponse:
        return ExtResponse(ok=True, status=status, data=data, retrieved_at=FIXED_NOW)

    @staticmethod
    def err(error: str, status: int | None = None) -> ExtResponse:
        return ExtResponse(ok=False, status=status, error=error, retrieved_at=FIXED_NOW)


class FakeHistory:
    """HistoryView over lists you pass in. Matching is exact; nothing is guessed."""

    def __init__(self, priors: list[PriorRecord] | None = None, payments: list[Payment] | None = None,
                 po_billed: dict[str, int] | None = None, contained: dict[str, list[str]] | None = None):
        self.priors = priors or []
        self.payments = payments or []
        self.po_billed = po_billed or {}
        self.contained = contained or {}

    def prior_by_invoice(self, vendor_id, invoice_no_canonical, *, before_item=None):
        return [p for p in self.priors if p.vendor_id == vendor_id and p.invoice_no_canonical == invoice_no_canonical]

    def prior_by_txn(self, key, amount_paise, currency, date_from, date_to, *, before_item=None):
        return [p for p in self.priors if p.amount_paise == amount_paise and p.currency == currency
                and p.date is not None and date_from <= p.date <= date_to and p.ref.startswith(key)]

    def prior_by_fingerprint(self, fingerprint, *, before_item=None):
        return [p for p in self.priors if p.ref == fingerprint]

    def payments_for_invoice(self, vendor_id, invoice_no_canonical):
        return [p for p in self.payments if p.item_id.endswith(f"{vendor_id}:{invoice_no_canonical}")]

    def po_billed_paise(self, po_number, *, before_item=None):
        return self.po_billed.get(po_number, 0)

    def open_critical_for_vendor(self, vendor_id):
        return self.contained.get(vendor_id, [])


class FakeLedger:
    def __init__(self):
        self.entries: dict[str, LedgerEntry] = {}

    def reserve(self, e: LedgerEntry) -> str:
        self.entries[e.call_key] = e
        return e.call_key

    def confirm(self, call_key, units, actual_inr, http_status, duration_ms):
        e = self.entries[call_key]
        self.entries[call_key] = e.model_copy(update={"status": "CONFIRMED", "units": units, "actual_inr": actual_inr,
                                                      "http_status": http_status, "duration_ms": duration_ms})

    def fail(self, call_key, http_status, error):
        self.entries[call_key] = self.entries[call_key].model_copy(update={"status": "FAILED", "http_status": http_status})

    def cache_hit(self, e: LedgerEntry) -> None:
        self.entries[e.call_key] = e.model_copy(update={"status": "CACHE_HIT"})


class FakeCache:
    def __init__(self):
        self.store: dict[tuple[str, str, str], CachedResponse] = {}

    def get(self, provider, operation, request_hash):
        return self.store.get((provider, operation, request_hash))

    def put(self, provider, operation, request_hash, resp, ttl_h):
        self.store[(provider, operation, request_hash)] = resp


class FakeBudget:
    """limit_inr=None -> unlimited. A None estimate reserves `unknown_reserve_inr` (reported cost stays UNKNOWN)."""

    def __init__(self, limit_inr: float | None = None, unknown_reserve_inr: float = 1.0, time_up: bool = False):
        self.limit_inr, self.unknown_reserve_inr, self.time_up = limit_inr, unknown_reserve_inr, time_up
        self.reserved = 0.0

    def reserve(self, estimated_inr):
        amount = self.unknown_reserve_inr if estimated_inr is None else estimated_inr
        if self.limit_inr is not None and self.reserved + amount > self.limit_inr:
            raise BudgetExceeded(f"{self.reserved + amount} > {self.limit_inr}")
        self.reserved += amount

    def check_time(self):
        if self.time_up:
            raise TimeExceeded("runtime limit")


def make_ctx(*, policy: Any = None, data: ClientData | None = None, ext: Any = None, history: Any = None,
             operation: Operation | None = Operation.POST_BANK_LINES, period: Period | None = None,
             cfg: RunConfig | None = None, client_id: str = "northbay") -> StageContext:
    """A StageContext made of fakes. Pass lane C's real PolicyConfig as `policy` once it exists."""
    cfg = cfg or RunConfig(limits={}, payments={}, approvals={}, costs={}, output={}, logging={}, storage={})
    policy = policy or SimpleNamespace(decision_hash_input="test-policy-hash", normalize=None)
    data = data or ClientData(client_id=client_id, vendors=[])
    return StageContext(run_id="R-test", session_id="S-test", client_id=client_id, operation=operation, period=period,
                        cfg=cfg, policy=policy, data=data, ext=ext or FakeExtClient(), history=history or FakeHistory(),
                        clock=fixed_clock)
