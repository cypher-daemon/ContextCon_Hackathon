"""Deterministic vendor resolution against the client's vendor master."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from rapidfuzz import fuzz

from paypilot.schemas import BankLine, DateBasis, Invoice, Resolution, Source, Vendor, VendorMessage
from paypilot.resolve.normalize import descriptor_key, name_key


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _policy(ctx: Any, key: str, default: Any) -> Any:
    policy = _value(ctx, "policy", {})
    section = _value(policy, key, {})
    return section if section else default


def _stable_source_id(record_ref: str, fact: str, value: str) -> str:
    raw = json.dumps([record_ref, fact, value], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _source(vendor: Vendor, fact: str, value: str, ctx: Any) -> Source:
    record_date = vendor.record_date
    return Source(
        source_id=_stable_source_id(vendor.record_ref, fact, value),
        provider="vendor_master", record_ref=vendor.record_ref,
        fact=fact, value=value, field_or_passage=fact.rsplit(".", 1)[-1],
        record_date=record_date,
        record_date_basis=DateBasis.RECORD_FIELD if record_date else DateBasis.UNKNOWN,
        retrieved_at=ctx.clock(),
        limitations=[] if record_date else ["vendor master has no record date"],
    )


def _result(status: str, method: str, *, vendor: Vendor | None = None, score: float | None = None,
            candidates: list[tuple[str, float]] | None = None, flags: list[str] | None = None,
            reasons: list[str] | None = None, evidence: list[Source] | None = None) -> Resolution:
    return Resolution(status=status, vendor_id=vendor.vendor_id if vendor else None,
                      legal_name=vendor.legal_name if vendor else None, method=method, score=score,
                      candidates=candidates or [], flags=flags or [], reasons=reasons or [], evidence=evidence or [])


def resolve_vendor(item: BankLine | Invoice | VendorMessage, ctx: Any) -> Resolution:
    """Resolve only unambiguous evidence; fuzzy evidence is always a held candidate."""
    vendors: list[Vendor] = list(_value(_value(ctx, "data", {}), "vendors", []) or [])
    norm = _policy(ctx, "normalize", {})
    match = _policy(ctx, "match", {})
    raw_name = _value(item, "vendor_name_raw")
    if raw_name is None:
        raw_name = _value(item, "descriptor_raw", "")
    query_name = name_key(raw_name or "", norm)
    query_descriptor = descriptor_key(_value(item, "descriptor_raw", raw_name or ""), norm)
    query_gstin = _value(item, "gstin")
    query_domain = (_value(item, "domain") or _value(item, "from_domain") or "").strip().lower().rstrip(".")
    query_fingerprint = _value(item, "account_fingerprint")

    # Identifier levels are authoritative only when exactly one vendor matches.
    for attr, query, method in (("gstin", query_gstin, "gstin"), ("cin", None, "cin"),
                                ("account_fingerprint", query_fingerprint, "account_fingerprint")):
        if method == "cin":
            query = _value(item, "cin")
        if query:
            hits = [v for v in vendors if _value(v, attr) and str(_value(v, attr)).casefold() == str(query).casefold()]
            if len(hits) == 1:
                v = hits[0]
                return _result("RESOLVED_EXACT_IDENTIFIER", method, vendor=v, score=100,
                               evidence=[_source(v, f"vendor.{attr}", str(_value(v, attr)), ctx)])
            if len(hits) > 1:
                return _result("TIE", method, candidates=[(v.vendor_id, 100.0) for v in hits],
                               flags=["PAYEE_TIE"], reasons=[f"Multiple vendor-master records share {method.upper()}"])

    # Exact domain may corroborate a name, but cannot alone prove the billed legal entity.
    identifier_domain_hits = [v for v in vendors if query_domain and v.domain and
                              v.domain.strip().lower().rstrip(".") == query_domain]

    def vendor_keys(v: Vendor) -> set[str]:
        return {name_key(v.legal_name, norm), *(name_key(a, norm) for a in v.aliases)} - {""}

    candidates = vendors
    if query_name:
        exact = [(v, 100.0) for v in candidates if query_name in vendor_keys(v)]
        # Bank descriptors often omit legal suffixes and may match the alias after processor cleanup.
        descriptor_name = name_key(query_descriptor, norm)
        if not exact and descriptor_name:
            exact = [(v, 100.0) for v in candidates if descriptor_name in vendor_keys(v)]
        if len(exact) == 1:
            v = exact[0][0]
            return _result("RESOLVED_VENDOR_MASTER", "exact", vendor=v, score=100,
                           evidence=[_source(v, "vendor.legal_name", v.legal_name, ctx)])
        if len(exact) > 1:
            return _result("TIE", "exact", candidates=[(v.vendor_id, score) for v, score in exact],
                           flags=["PAYEE_TIE"], reasons=["Multiple vendors match the normalized name"])

    # Domain agreement can disambiguate only after exact name/alias agreement.
    if identifier_domain_hits and query_name:
        domain_exact = [(v, 100.0) for v in identifier_domain_hits if query_name in vendor_keys(v)]
        if len(domain_exact) == 1:
            v = domain_exact[0][0]
            return _result("RESOLVED_VENDOR_MASTER", "exact", vendor=v, score=100,
                           evidence=[_source(v, "vendor.legal_name", v.legal_name, ctx),
                                     _source(v, "vendor.domain", v.domain, ctx)])

    alias = [(v, 100.0) for v in candidates if any(name_key(a, norm) == query_name for a in v.aliases)] if query_name else []
    if len(alias) == 1:
        v = alias[0][0]
        return _result("RESOLVED_VENDOR_MASTER", "alias", vendor=v, score=100,
                       evidence=[_source(v, "vendor.aliases", "|".join(v.aliases), ctx)])
    if len(alias) > 1:
        return _result("TIE", "alias", candidates=[(v.vendor_id, s) for v, s in alias], flags=["PAYEE_TIE"],
                       reasons=["Multiple vendors share the normalized alias"])

    # A near-name result is explicitly non-resolving, including token supersets/lookalikes.
    scores: list[tuple[Vendor, float]] = []
    if query_name:
        for v in vendors:
            score = max((float(max(fuzz.ratio(query_name, key), fuzz.partial_ratio(query_name, key)))
                         for key in vendor_keys(v)), default=0.0)
            scores.append((v, score))
    scores.sort(key=lambda pair: (-pair[1], pair[0].vendor_id))
    if scores:
        best = scores[0][1]
        second = scores[1][1] if len(scores) > 1 else 0.0
        floor = float(_value(match, "lookalike_floor", 75))
        threshold = float(_value(match, "fuzzy_min_score", 92))
        margin = float(_value(match, "fuzzy_min_margin", 5))
        band = [(v, score) for v, score in scores if score >= max(floor, best - margin)]
        if len(band) > 1 and best >= floor and best - band[1][1] < margin:
            return _result("TIE", "fuzzy", candidates=[(v.vendor_id, s) for v, s in band],
                           flags=["PAYEE_TIE"], reasons=["Multiple vendor names are equally plausible"])
        if best >= floor:
            v = scores[0][0]
            full_name_score = max((float(fuzz.ratio(query_name, key)) for key in vendor_keys(v)), default=0.0)
            looks = full_name_score < threshold or (second >= floor and best - second < margin)
            return _result("LOOKALIKE" if looks else "FUZZY_CANDIDATE", "fuzzy", vendor=v, score=best,
                           candidates=[(candidate.vendor_id, score) for candidate, score in scores if score >= floor],
                           flags=["LOOKALIKE" if looks else "FUZZY_MATCH"],
                           reasons=["Name similarity is not sufficient to establish vendor identity"],
                           evidence=[_source(v, "vendor.legal_name", v.legal_name, ctx)])

    return _result("UNRESOLVED", "none", flags=["PAYEE_UNRESOLVED"],
                   reasons=["No vendor-master record matches the supplied name or identifier"])
