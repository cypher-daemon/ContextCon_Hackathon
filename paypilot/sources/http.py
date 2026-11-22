"""HTTP-backed ExtClient. All external I/O and its accounting live in this module."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

from paypilot.context import BudgetExceeded, TimeExceeded
from paypilot.schemas import CachedResponse, ExtResponse, LedgerEntry, RunConfig


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    # Query strings may carry credentials. Retain only the endpoint in durable logs/keys.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _headers(headers: Any) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        if str(key).lower() not in {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"}:
            safe[str(key)] = str(value)
    return safe


def _error(status: int | None, exc: Exception | None = None) -> str:
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "TIMEOUT"
    if status == 402:
        return "CREDITS_EXHAUSTED"
    if status == 429:
        return "RATE_LIMITED"
    if status == 401:
        return "AUTH"
    if status == 403:
        return "FORBIDDEN"
    if status == 404:
        return "NOT_FOUND"
    if status is not None and status >= 500:
        return "SERVER"
    return "BAD_BODY" if status is not None and 200 <= status < 300 else "SERVER"


class _TokenBucket:
    def __init__(self, per_minute: float):
        self.rate = max(float(per_minute), 0.001) / 60.0
        self.capacity = max(1.0, float(per_minute))
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                delay = (1 - self.tokens) / self.rate
            time.sleep(delay)


class _HttpExtClient:
    def __init__(self, cfg: RunConfig, ledger: Any, cache: Any, budget: Any):
        self.cfg, self.ledger, self.cache, self.budget = cfg, ledger, cache, budget
        self._buckets: dict[tuple[str, str], _TokenBucket] = {}
        self._lock = threading.Lock()

    def get_json(self, provider: str, operation: str, url: str, *, params: dict | None = None,
                 item_id: str | None, stage: str) -> ExtResponse:
        return self._request("GET", provider, operation, url, params=params, item_id=item_id, stage=stage)

    def post_json(self, provider: str, operation: str, url: str, body: dict, *, item_id: str | None,
                  stage: str) -> ExtResponse:
        return self._request("POST", provider, operation, url, body=body, item_id=item_id, stage=stage)

    def fetch_page(self, url: str, *, item_id: str | None, stage: str) -> ExtResponse:
        return self._request("GET", "web", "fetch_page", url, item_id=item_id, stage=stage, want_json=False)

    def _request(self, method: str, provider: str, operation: str, url: str, *, item_id: str | None,
                 stage: str, params: dict | None = None, body: dict | None = None,
                 want_json: bool = True) -> ExtResponse:
        now = datetime.now(timezone.utc)
        safe_url = _safe_url(url)
        request_hash = _hash([method, provider, operation, safe_url, params or {}, body or {}])
        if params:
            # Include query params in actual request without putting them in durable logs.
            request_url = url
        else:
            request_url = url
        cached = self.cache.get(provider, operation, request_hash)
        if cached is not None:
            return self._cached(cached, request_hash)

        settings = _get(_get(self.cfg, "sources", {}), provider, {})
        rate = _get(_get(settings, "rate_per_min", {}), operation, None)
        if rate is not None:
            key = (provider, operation)
            with self._lock:
                bucket = self._buckets.setdefault(key, _TokenBucket(rate))
            bucket.wait()
        retries = int(_get(settings, "retries", 0) or 0)
        backoff = float(_get(settings, "backoff_base_s", 0.5) or 0.5)
        ttl = _get(settings, "cache_ttl_h", None)
        cost_cfg = _get(self.cfg, "costs", {})
        provider_cost = _get(cost_cfg, provider, {})
        estimate = _get(_get(provider_cost, "estimates", {}), operation, None)
        if estimate is None and provider == "ifsc":
            estimate = 0.0
        units = 1.0
        unit_type = "credits" if provider == "crustdata" else "call"
        attempt = 0
        while True:
            attempt += 1
            call_key = _hash([self._run_id(stage), item_id, request_hash, attempt])
            entry = LedgerEntry(call_key=call_key, run_id=self._run_id(stage), session_id=self._session_id(stage),
                                item_id=item_id, stage=stage, provider=provider, operation=operation,
                                unit_type=unit_type, estimated_inr=estimate, status="RESERVED",
                                request_hash=request_hash, attempt=attempt, ts=now)
            try:
                self.budget.check_time()
                self.budget.reserve(estimate)
                self.ledger.reserve(entry)
            except (BudgetExceeded, TimeExceeded):
                return ExtResponse(ok=False, error="BUDGET", retrieved_at=now, request_hash=request_hash)
            start = time.monotonic()
            response = None
            error: Exception | None = None
            try:
                headers: dict[str, str] = {}
                api_version = _get(settings, "api_version")
                if api_version and provider == "crustdata":
                    headers["x-api-version"] = str(api_version)
                response = httpx.request(method, request_url, params=params, json=body, headers=headers,
                                         timeout=float(_get(self.cfg.limits, "call_timeout_s", 20)))
                status = response.status_code
                response_headers = _headers(response.headers)
                if want_json:
                    try:
                        data = response.json()
                    except (ValueError, json.JSONDecodeError):
                        data = None
                        error = ValueError("invalid JSON response")
                else:
                    data = response.text
            except (httpx.HTTPError, TimeoutError) as exc:
                status, response_headers, data, error = None, {}, None, exc
            duration = int((time.monotonic() - start) * 1000)
            retrieved = self._retrieved_at(response_headers, now)
            mapped = _error(status, error) if error or status is None or status >= 400 else None
            self._finish(call_key, status, duration, mapped)
            if mapped is None:
                cached_response = CachedResponse(status=status, headers=response_headers,
                                                 body=json.dumps(data, ensure_ascii=False) if want_json else str(data),
                                                 retrieved_at=retrieved)
                self.cache.put(provider, operation, request_hash, cached_response, ttl)
                return ExtResponse(ok=True, status=status, data=data, headers=response_headers,
                                   retrieved_at=retrieved, request_hash=request_hash)
            if mapped in {"CREDITS_EXHAUSTED", "AUTH", "FORBIDDEN", "NOT_FOUND", "BAD_BODY"}:
                return ExtResponse(ok=False, status=status, headers=response_headers, error=mapped,
                                   retrieved_at=retrieved, request_hash=request_hash)
            if attempt > retries:
                return ExtResponse(ok=False, status=status, headers=response_headers, error=mapped,
                                   retrieved_at=retrieved, request_hash=request_hash)
            delay = self._retry_delay(attempt, backoff, response_headers)
            time.sleep(delay)

    def _finish(self, call_key: str, status: int | None, duration: int, error: str | None) -> None:
        if error:
            self.ledger.fail(call_key, status, error)
        else:
            self.ledger.confirm(call_key, 1.0, None, status, duration)

    @staticmethod
    def _retrieved_at(headers: dict[str, str], fallback: datetime) -> datetime:
        value = next((v for k, v in headers.items() if k.lower() == "date"), None)
        if value:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
        return fallback

    @staticmethod
    def _retry_delay(attempt: int, base: float, headers: dict[str, str]) -> float:
        lowered = {k.lower(): v for k, v in headers.items()}
        reset = lowered.get("x-ratelimit-reset") if "x-ratelimit-reset" in lowered else lowered.get("retry-after")
        if reset:
            try:
                return max(0.0, min(float(reset), 60.0))
            except ValueError:
                try:
                    return max(0.0, min((parsedate_to_datetime(reset) - datetime.now(timezone.utc)).total_seconds(), 60.0))
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(60.0, base * (2 ** (attempt - 1)) * random.uniform(0.8, 1.2))

    @staticmethod
    def _cached(cached: CachedResponse, request_hash: str) -> ExtResponse:
        try:
            data = json.loads(cached.body)
        except (ValueError, json.JSONDecodeError):
            data = cached.body
        return ExtResponse(ok=200 <= cached.status < 300, status=cached.status, data=data,
                           headers=_headers(cached.headers), cache_hit=True,
                           error=None if 200 <= cached.status < 300 else _error(cached.status),
                           retrieved_at=cached.retrieved_at, request_hash=request_hash)

    @staticmethod
    def _run_id(stage: str) -> str:
        return str(getattr(stage, "run_id", "unknown"))

    @staticmethod
    def _session_id(stage: str) -> str:
        return str(getattr(stage, "session_id", "unknown"))


def make_ext_client(cfg: RunConfig, ledger: Any, cache: Any, budget: Any) -> Any:
    """Build an ExtClient using only the runtime-supplied ledger, cache and budget ports."""
    return _HttpExtClient(cfg, ledger, cache, budget)
