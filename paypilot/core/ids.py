"""Deterministic identity formulas (INTERFACES §5). Owner: lane A. Same input -> same id, so restarts are idempotent."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

from paypilot.schemas import Facts

PAYPILOT_NS = uuid.UUID("6f1c3b0e-5a52-4f0e-9d7a-0c2f6a1b9e11")  # fixed namespace for idempotency keys; never change


def canonical_json(obj: Any) -> str:
    """Stable JSON: sorted keys, no whitespace, non-ASCII kept. Pydantic models are dumped in JSON mode first."""
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(*parts: Any) -> str:
    """Hash an unambiguous encoding (a JSON array), so ("a|b","c") != ("a","b|c")."""
    return hashlib.sha256(json.dumps([str(p) for p in parts], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def run_id(client_id: str, request_sha: str) -> str:
    return "R-" + sha256_hex(client_id, request_sha)[:8]


def item_id(client_id: str, request_sha: str, input_name: str, row_no: int) -> str:
    return sha256_hex(client_id, request_sha, input_name, row_no)[:20]


def fingerprint_account(raw_account: str, hmac_key: str) -> str:
    """HMAC-SHA256 of the digits-only account number. The raw number is never stored or logged."""
    digits = "".join(ch for ch in raw_account if ch.isalnum()).upper()
    return hmac.new(hmac_key.encode(), digits.encode(), hashlib.sha256).hexdigest()


def txn_fingerprint_bank_line(*, bank_reference: str | None, descriptor_key: str, amount_paise: int,
                              currency: str, date: Any, direction: str) -> str:
    if bank_reference:
        return sha256_hex("ref", bank_reference)
    return sha256_hex(descriptor_key, amount_paise, currency, date, direction)


def txn_fingerprint_invoice(vendor_name_key: str, invoice_no_canonical: str | None) -> str:
    return sha256_hex(vendor_name_key, invoice_no_canonical or "")


def payment_key(client_id: str, vendor_id: str, invoice_no_canonical: str, amount_paise: int, currency: str) -> str:
    """IMMUTABLE. No approval id, version or timestamp may ever enter this key."""
    if not invoice_no_canonical:
        raise ValueError("payment_key needs an invoice number (item is CANNOT_VERIFY / MISSING_FIELD)")
    return "pp_" + sha256_hex(client_id, vendor_id, invoice_no_canonical, amount_paise, currency)[:32]


def attempt_key(pay_key: str, n: int) -> str:
    return f"{pay_key}:a{n}"


def step_key(att_key: str, step: str) -> str:
    assert step in ("create", "confirm")
    return f"{att_key}:{step}"


def razorpayx_idempotency_key(att_key: str) -> str:
    """RazorpayX allows 4-36 chars; derive deterministically (uuid5 = 36 chars), never random."""
    return str(uuid.uuid5(PAYPILOT_NS, f"{att_key}:create"))


def decision_hash(facts: Facts, decision_hash_input: str) -> str:
    """sha256(canonical_json(facts) | policy.decision_hash_input). No timestamps, source ids or output/limit config."""
    f = facts.model_copy(update={"flags": sorted(facts.flags)})
    return sha256_hex(canonical_json(f), decision_hash_input)


def approval_id(item: str, dec_hash: str, approval_seq: int) -> str:
    return sha256_hex(item, dec_hash, approval_seq)[:16]


def source_id(provider: str, record_ref: str, fact: str, field_or_passage: str) -> str:
    return sha256_hex(provider, record_ref, fact, field_or_passage)[:16]


def exception_id(item: str | None, payment: str | None, type_: str, origin_key: str) -> str:
    return sha256_hex(item or "", payment or "", type_, origin_key)[:16]


def call_key(run: str, item: str | None, stage: str, provider: str, operation: str, request_hash: str, attempt: int) -> str:
    return sha256_hex(run, item or "", stage, provider, operation, request_hash, attempt)
