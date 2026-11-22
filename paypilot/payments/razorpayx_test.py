"""RazorpayX Payouts TEST-MODE adapter (A9, INTERFACES §8). Owner: lane A.

Test mode only: the key id must start with `rzp_test_`; anything else is refused, there is no live option and no fallback.
Secrets come from .env only and are never logged. The raw bank account is built into the request body in memory and is never logged,
stored or hashed into a request hash. The REST API is called with httpx (no provider SDK).

  create   POST /v1/payouts (composite: contact + fund account + payout), X-Payout-Idempotency derived from the attempt key
  confirm  GET  /v1/payouts/{id} (no money-moving call; the payout is already initiated by create)
  retrieve GET  /v1/payouts/{id}
  find_by_attempt_key  replays the identical create (same key, byte-identical body) while younger than idem_replay_window_h; else None
  refund   NotSupported
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from paypilot.context import NotSupported, ProviderStatusUnknown
from paypilot.core import ids
from paypilot.payments.provider import ProviderRejected, ProviderTimeout
from paypilot.schemas import ProviderPayment, ProviderStatus, RunConfig

_STATUS = {"queued": ProviderStatus.PROCESSING, "pending": ProviderStatus.PROCESSING, "scheduled": ProviderStatus.PROCESSING,
           "processing": ProviderStatus.PROCESSING, "processed": ProviderStatus.SUCCEEDED, "rejected": ProviderStatus.DECLINED,
           "cancelled": ProviderStatus.CANCELED, "canceled": ProviderStatus.CANCELED, "failed": ProviderStatus.DECLINED,
           "reversed": ProviderStatus.RETURNED}


class ConfigRefused(Exception):
    """razorpayx_test is not configured safely (CLI exit 2)."""


def map_status(raw: Any) -> ProviderStatus:
    """Documented RazorpayX status -> ProviderStatus. Anything else raises: it becomes PAYMENT_UNCERTAIN, never success."""
    if not isinstance(raw, str) or raw.lower() not in _STATUS:
        raise ProviderStatusUnknown(f"unrecognised RazorpayX payout status {raw!r}")
    return _STATUS[raw.lower()]


def check_credentials(env: dict[str, str] | None = None) -> tuple[str, str, str]:
    env = os.environ if env is None else env
    key, secret, acct = env.get("RAZORPAYX_KEY_ID", ""), env.get("RAZORPAYX_KEY_SECRET", ""), env.get("RAZORPAYX_ACCOUNT_NUMBER", "")
    if not key.startswith("rzp_test_"):
        raise ConfigRefused("razorpayx_test needs RAZORPAYX_KEY_ID starting with rzp_test_ (live keys are refused)")
    if not secret or not acct:
        raise ConfigRefused("razorpayx_test needs RAZORPAYX_KEY_SECRET and RAZORPAYX_ACCOUNT_NUMBER in .env")
    return key, secret, acct


class RazorpayXTestProvider:
    name = "razorpayx_test"
    needs_bank_details = True

    def __init__(self, key_id: str, key_secret: str, account_number: str, *, base_url: str = "https://api.razorpay.com/v1",
                 mode: str = "IMPS", purpose: str = "vendor bill", replay_window_h: float = 24, timeout_s: float = 20,
                 transport: httpx.BaseTransport | None = None, clock: Callable[[], datetime] | None = None):
        if not key_id.startswith("rzp_test_"):
            raise ConfigRefused("refusing a non rzp_test_ key: this adapter is test mode only")
        self._auth = (key_id, key_secret)
        self.account_number, self.base_url, self.mode, self.purpose = account_number, base_url.rstrip("/"), mode, purpose
        self.replay_window_h, self.timeout_s = replay_window_h, timeout_s
        self._client = httpx.Client(transport=transport, timeout=timeout_s)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.context_for: Callable[[str], dict] | None = None      # set by the executor: attempt_key -> amount, currency, metadata (+ raw account)

    @classmethod
    def from_env(cls, cfg: RunConfig) -> "RazorpayXTestProvider":
        key, secret, acct = check_credentials()
        rc = cfg.payments.get("razorpayx_test", {})
        return cls(key, secret, acct, base_url=rc.get("base_url", "https://api.razorpay.com/v1"), mode=rc.get("mode", "IMPS"),
                   purpose=rc.get("purpose", "vendor bill"), replay_window_h=float(rc.get("idem_replay_window_h", 24)),
                   timeout_s=float(rc.get("call_timeout_s", 20)))

    # -- http --
    def _call(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None) -> dict:
        try:
            r = self._client.request(method, self.base_url + path, content=body, auth=self._auth,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderTimeout(f"RazorpayX {method} {path}: {type(e).__name__}") from e
        if r.status_code >= 500:
            raise ProviderTimeout(f"RazorpayX {method} {path}: HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise ProviderStatusUnknown(f"RazorpayX {method} {path}: non-JSON body (HTTP {r.status_code})")
        if r.status_code >= 400:
            desc = (data.get("error") or {}).get("description", "") if isinstance(data, dict) else ""
            raise ProviderRejected(f"RazorpayX {method} {path}: HTTP {r.status_code} {desc[:200]}")      # never the body, never secrets
        return data

    def _pp(self, d: dict, fallback_amount: int = 0) -> ProviderPayment:
        if not isinstance(d, dict) or not d.get("id"):
            raise ProviderStatusUnknown("RazorpayX response has no payout id")
        return ProviderPayment(ref=d["id"], status=map_status(d.get("status")), amount_paise=int(d.get("amount", fallback_amount)),
                               currency=str(d.get("currency", "INR")), metadata={"utr": d.get("utr"), "raw_status": d.get("status")},
                               raw_ref=d["id"])

    def _body(self, attempt_key: str, amount_paise: int, currency: str, md: dict) -> bytes:
        """Deterministic: the same attempt always produces the same bytes (a replayed key with a different body is rejected)."""
        body = {"account_number": self.account_number, "amount": amount_paise, "currency": currency, "mode": self.mode,
                "purpose": self.purpose, "queue_if_low_balance": True, "reference_id": attempt_key[-40:], "narration": "PayPilot"[:30],
                "fund_account": {"account_type": "bank_account",
                                 "bank_account": {"name": md["vendor_name"], "ifsc": md["ifsc"], "account_number": md["account_number"]},
                                 "contact": {"name": md["vendor_name"], "type": "vendor", "reference_id": md["vendor_id"]}}}
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    # -- PaymentProvider --
    def create(self, key: str, amount_paise: int, currency: str, metadata: dict) -> ProviderPayment:
        if metadata.get("account_number") is None:
            raise ProviderRejected("no bank details supplied for the payout")
        return self._pp(self._call("POST", "/payouts", body=self._body(key, amount_paise, currency, metadata),
                                   headers={"X-Payout-Idempotency": ids.razorpayx_idempotency_key(key)}), amount_paise)

    def confirm(self, provider_ref: str, key: str) -> ProviderPayment:
        return self.retrieve(provider_ref)                    # no money-moving call: the payout was initiated by create

    def retrieve(self, provider_ref: str) -> ProviderPayment:
        return self._pp(self._call("GET", f"/payouts/{provider_ref}"))

    def find_by_attempt_key(self, attempt_key: str) -> ProviderPayment | None:
        """No search endpoint exists. Replay the identical create within the window (same key + same body = the same payout);
        outside it, or with no context to rebuild the body, return None WITHOUT any call (-> PAYMENT_UNCERTAIN, never a new create)."""
        if self.context_for is None:
            return None
        ctx = self.context_for(attempt_key)
        age_h = (self._clock() - ctx["attempt_created_at"]).total_seconds() / 3600
        if age_h > self.replay_window_h:
            return None
        return self.create(attempt_key, ctx["amount_paise"], ctx["currency"], ctx["metadata"])

    def refund(self, provider_ref: str, key: str) -> ProviderPayment:
        raise NotSupported("RazorpayX payouts are not refunded; recovery is a human-led case")
