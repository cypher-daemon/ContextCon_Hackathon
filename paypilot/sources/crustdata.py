"""Crustdata Company Identify adapter. Requests are strictly domain-based."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from types import SimpleNamespace
from typing import Any

from paypilot.schemas import DateBasis, Source


def _source(ctx: Any, domain: str, company: dict[str, Any], record: dict[str, Any], item_id: str) -> Source:
    info = company.get("basic_info") or {}
    name = str(info.get("name") or "")
    cid = str(company.get("crustdata_company_id") or "unknown")
    record_ref = f"crustdata://company/{cid}?domain={domain}"
    fact_value = json.dumps({"name": name, "domain": domain}, sort_keys=True)
    source_id = hashlib.sha256(f"{record_ref}|{fact_value}".encode()).hexdigest()[:20]
    raw_date = company.get("updated_at") or record.get("updated_at") or (company.get("metadata") or {}).get("updated_at")
    record_date = None
    if raw_date:
        try:
            record_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            pass
    retrieved_at = getattr(record.get("_response"), "retrieved_at", None) or ctx.clock()
    return Source(source_id=source_id, provider="crustdata_identify", record_ref=record_ref,
                  fact="company.domain_association", value=fact_value,
                  field_or_passage=f"domain={domain}; company_name={name}; company_id={cid}",
                  record_date=record_date, record_date_basis=DateBasis.RECORD_FIELD if record_date else DateBasis.UNKNOWN,
                  retrieved_at=retrieved_at,
                  limitations=["Domain association does not establish the billed legal entity"])


def identify_by_domain(ctx: Any, domain: str, item_id: str) -> Any:
    """Identify a company from a known domain; never call Crustdata by name."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "/" in domain or "@" in domain:
        return SimpleNamespace(status="UNRESOLVED", domain=domain, matches=[], evidence=[],
                               error="A valid domain is required")
    cfg = ctx.policy.sources.crustdata
    if not cfg.enabled:
        return SimpleNamespace(status="SOURCE_UNAVAILABLE", domain=domain, matches=[], evidence=[],
                               error="Crustdata is disabled")
    url = f"{cfg.base_url.rstrip('/')}/company/identify"
    response = ctx.ext.post_json("crustdata", "identify", url, {"domains": [domain]},
                                 item_id=item_id, stage="resolve")
    if not response.ok:
        return SimpleNamespace(status="SOURCE_UNAVAILABLE", domain=domain, matches=[], evidence=[],
                               error=response.error or f"HTTP {response.status}")
    payload = response.data
    if not isinstance(payload, list) or not payload:
        return SimpleNamespace(status="UNRESOLVED", domain=domain, matches=[], evidence=[], error="No company match")
    matching: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        for entry in row.get("matches") or []:
            if not isinstance(entry, dict):
                continue
            company = entry.get("company_data") or {}
            info = company.get("basic_info") or {}
            returned_domain = str(info.get("domain") or company.get("domain") or "").lower().rstrip(".")
            # API identify response is domain-scoped; when it explicitly names a different domain, reject it.
            if returned_domain and returned_domain != domain:
                continue
            try:
                score = float(entry.get("confidence_score", 0))
            except (TypeError, ValueError):
                score = 0.0
            if info.get("name") or returned_domain:
                matching.append((score, company, row))
    matching.sort(key=lambda entry: (-entry[0], str((entry[1].get("basic_info") or {}).get("name", "")).casefold()))
    if not matching:
        return SimpleNamespace(status="UNRESOLVED", domain=domain, matches=[], evidence=[], error="No usable company data")
    top_score = matching[0][0]
    top = [entry for entry in matching if entry[0] == top_score]
    items = []
    sources = []
    for score, company, row in top:
        row = dict(row)
        row["_response"] = response
        name = str((company.get("basic_info") or {}).get("name") or "")
        items.append({"name": name, "domain": domain, "confidence_score": score,
                      "company_id": company.get("crustdata_company_id")})
        sources.append(_source(ctx, domain, company, row, item_id))
    status = "MATCH" if len(items) == 1 else "TIE"
    return SimpleNamespace(status=status, domain=domain, matches=items, evidence=sources,
                           error=None, parent_brand_warning=(len(items) == 1 and bool(items[0]["name"])))
