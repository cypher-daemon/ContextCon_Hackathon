"""FakeProvider (A8): the default provider and the test target. Owner: lane A.

Its own SQLite database (a separate "bank side"), so a test can ask it how many successes happened for a key, independently of
PayPilot's database. `create` is idempotent on the key. Fault injection via $PAYPILOT_FAKE_FAULT (read at call time):

  TIMEOUT_AFTER_CREATE   the payment is recorded provider-side, then the call raises ProviderTimeout (lost response)
  TIMEOUT_BEFORE_CREATE  the call raises ProviderTimeout and nothing is recorded
  DECLINE                confirm declines
  PROCESSING             confirm leaves it PROCESSING (non-terminal); `force_status` / `fake-provider set` moves it later
  RETURNED_AFTER_SUCCESS confirm succeeds; the next retrieve reports RETURNED (a reversal after success)
  UNKNOWN_STATUS         retrieve returns a status PayPilot does not recognise -> ProviderStatusUnknown
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paypilot.context import NotSupported, ProviderStatusUnknown
from paypilot.payments.provider import ProviderTimeout
from paypilot.schemas import ProviderPayment, ProviderStatus

FAULT_ENV = "PAYPILOT_FAKE_FAULT"
FAULTS = ("TIMEOUT_AFTER_CREATE", "TIMEOUT_BEFORE_CREATE", "DECLINE", "PROCESSING", "RETURNED_AFTER_SUCCESS", "UNKNOWN_STATUS")
_DDL = """CREATE TABLE IF NOT EXISTS fake_payments (ref TEXT PRIMARY KEY, idem_key TEXT NOT NULL UNIQUE, amount_paise INTEGER NOT NULL,
  currency TEXT NOT NULL, status TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, success_count INTEGER NOT NULL DEFAULT 0);"""


def _fault() -> str | None:
    f = os.environ.get(FAULT_ENV) or None
    if f is not None and f not in FAULTS:
        raise ValueError(f"{FAULT_ENV}={f!r} is not a known fault; valid: {', '.join(FAULTS)}")
    return f


class FakeProvider:
    name = "fake"
    needs_bank_details = False

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=FULL")
        self.con.executescript(_DDL)

    def close(self) -> None:
        self.con.close()

    # -- helpers --
    def _pp(self, r: sqlite3.Row, status: str | None = None) -> ProviderPayment:
        s = status or r["status"]
        try:
            st = ProviderStatus(s)
        except ValueError:
            raise ProviderStatusUnknown(f"fake provider returned unrecognised status {s!r}")
        return ProviderPayment(ref=r["ref"], status=st, amount_paise=r["amount_paise"], currency=r["currency"],
                               metadata=json.loads(r["metadata_json"]), raw_ref=r["ref"])

    def _get(self, ref: str) -> sqlite3.Row:
        r = self.con.execute("SELECT * FROM fake_payments WHERE ref=?", (ref,)).fetchone()
        if r is None:
            raise KeyError(f"fake provider has no payment {ref}")
        return r

    def _set(self, ref: str, status: str) -> None:
        self.con.execute("UPDATE fake_payments SET status=?, success_count=success_count + ? WHERE ref=?",
                         (status, 1 if status == "SUCCEEDED" else 0, ref))

    # -- PaymentProvider --
    def create(self, key: str, amount_paise: int, currency: str, metadata: dict) -> ProviderPayment:
        fault = _fault()
        if fault == "TIMEOUT_BEFORE_CREATE":
            raise ProviderTimeout("fake provider timed out before creating the payment")
        row = self.con.execute("SELECT * FROM fake_payments WHERE idem_key=?", (key,)).fetchone()
        if row is None:                                     # idempotent: the same key never creates a second payment
            safe = {k: v for k, v in metadata.items() if k not in ("account_number",)}
            self.con.execute("INSERT INTO fake_payments(ref,idem_key,amount_paise,currency,status,metadata_json,created_at) VALUES (?,?,?,?,?,?,?)",
                             ("fp_" + uuid.uuid4().hex[:16], key, amount_paise, currency, "REQUIRES_CONFIRMATION", json.dumps(safe, default=str),
                              datetime.now(timezone.utc).isoformat()))
            row = self.con.execute("SELECT * FROM fake_payments WHERE idem_key=?", (key,)).fetchone()
        if fault == "TIMEOUT_AFTER_CREATE":
            raise ProviderTimeout("fake provider timed out after creating the payment (response lost)")
        return self._pp(row)

    def confirm(self, provider_ref: str, key: str) -> ProviderPayment:
        r = self._get(provider_ref)
        if r["status"] == "REQUIRES_CONFIRMATION":
            fault = _fault()
            self._set(provider_ref, "DECLINED" if fault == "DECLINE" else "PROCESSING" if fault == "PROCESSING" else "SUCCEEDED")
        return self._pp(self._get(provider_ref))

    def retrieve(self, provider_ref: str) -> ProviderPayment:
        r = self._get(provider_ref)
        fault = _fault()
        if fault == "UNKNOWN_STATUS":
            return self._pp(r, "WEIRD_STATE")
        if fault == "RETURNED_AFTER_SUCCESS" and r["status"] == "SUCCEEDED" and not r["status"] == "RETURNED":
            # the bank later returns the money; the first retrieve after the success reports it
            self._set(provider_ref, "RETURNED")
            r = self._get(provider_ref)
        return self._pp(r)

    def find_by_attempt_key(self, attempt_key: str) -> ProviderPayment | None:
        r = self.con.execute("SELECT * FROM fake_payments WHERE idem_key=?", (attempt_key,)).fetchone()
        return self._pp(r) if r is not None else None

    def refund(self, provider_ref: str, key: str) -> ProviderPayment:
        raise NotSupported("the fake provider does not refund; recovery is a human-led case")

    # -- test / demo helpers (not part of the port) --
    def force_status(self, provider_ref: str, status: str) -> None:
        """Move a payment by hand, like the RazorpayX test Dashboard does."""
        ProviderStatus(status)
        self._set(provider_ref, status)

    def success_count(self, key: str) -> int:
        r = self.con.execute("SELECT success_count FROM fake_payments WHERE idem_key=?", (key,)).fetchone()
        return int(r["success_count"]) if r else 0

    def total_payments(self) -> int:
        return int(self.con.execute("SELECT COUNT(*) FROM fake_payments").fetchone()[0])
