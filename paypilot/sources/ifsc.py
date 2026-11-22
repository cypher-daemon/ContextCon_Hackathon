"""Optional public IFSC branch lookup."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from paypilot.schemas import DateBasis, Source


def _source_id(ref: str, value: str) -> str:
    return hashlib.sha256(json.dumps([ref, value], separators=(",", ":")).encode()).hexdigest()[:20]


def lookup(ctx: Any, ifsc: str, item_id: str) -> Any:
    """Return VALID, NOT_FOUND, or UNAVAILABLE; never infer account ownership."""
    from types import SimpleNamespace

    code = (ifsc or "").strip().upper()
    if not code:
        return SimpleNamespace(status="UNAVAILABLE", ifsc_valid="UNKNOWN", bank=None, branch=None,
                               evidence=[], reason="IFSC is missing")
    cfg = ctx.policy.sources.ifsc
    if not cfg.enabled:
        return SimpleNamespace(status="UNAVAILABLE", ifsc_valid="NOT_CHECKED", bank=None, branch=None,
                               evidence=[], reason="IFSC lookup is disabled")
    response = ctx.ext.get_json("ifsc", "lookup", f"{cfg.base_url.rstrip('/')}/{code}",
                                item_id=item_id, stage="check")
    if not response.ok:
        if response.error == "NOT_FOUND" or response.status == 404:
            return SimpleNamespace(status="NOT_FOUND", ifsc_valid="FAIL", bank=None, branch=None,
                                   evidence=[], reason="IFSC was not found")
        return SimpleNamespace(status="UNAVAILABLE", ifsc_valid="UNKNOWN", bank=None, branch=None,
                               evidence=[], reason=f"IFSC source unavailable: {response.error or response.status}")
    data = response.data if isinstance(response.data, dict) else {}
    if not data.get("IFSC") or str(data["IFSC"]).upper() != code:
        return SimpleNamespace(status="UNAVAILABLE", ifsc_valid="UNKNOWN", bank=None, branch=None,
                               evidence=[], reason="IFSC response was empty or did not match the requested code")
    retrieved_at = response.retrieved_at or ctx.clock()
    ref = f"https://ifsc.razorpay.com/{code}"
    evidence = [Source(source_id=_source_id(ref, code), provider="razorpay_ifsc", record_ref=ref,
                       fact="bank.ifsc", value=code, field_or_passage=f"IFSC: {code}", record_date=None,
                       record_date_basis=DateBasis.UNKNOWN, retrieved_at=retrieved_at,
                       limitations=["Proves branch code metadata only; does not prove account ownership"])]
    return SimpleNamespace(status="VALID", ifsc_valid="PASS", bank=data.get("BANK"), branch=data.get("BRANCH"),
                           address=data.get("ADDRESS"), evidence=evidence, reason=None)
