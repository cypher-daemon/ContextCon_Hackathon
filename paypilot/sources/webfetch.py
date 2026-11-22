"""Fetch self-published company identity claims from configured website pages."""
from __future__ import annotations

import hashlib
import re
from datetime import date
from html import unescape
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

from paypilot.schemas import DateBasis, Source


def _host(domain: str) -> str:
    value = (domain or "").strip().lower().rstrip(".")
    value = re.sub(r"^https?://", "", value).split("/", 1)[0].split(":", 1)[0]
    return value.removeprefix("www.")


def _blocked_host(host: str, configured: list[str]) -> bool:
    return any(host == _host(entry) or host.endswith("." + _host(entry)) for entry in configured)


def _text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</footer>", "\n", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def _publication_date(text: str) -> date | None:
    match = re.search(r"(?:published|updated|last modified)\s*(?:on|:)?\s*(\d{4}-\d{2}-\d{2})", text, re.I)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    return None


def fetch_identity_claim(ctx: Any, domain: str, item_id: str) -> Any | None:
    """Return a citable CIN/legal-name passage, or None when no claim is found."""
    host = _host(domain)
    if not host:
        return None
    cfg = ctx.policy.sources.webfetch
    if not cfg.enabled or _blocked_host(host, cfg.skip_domains):
        return None
    markers = [str(marker).casefold() for marker in cfg.cf_markers]
    for path in cfg.paths:
        url = urljoin(f"https://{host}/", str(path).lstrip("/"))
        response = ctx.ext.fetch_page(url, item_id=item_id, stage="resolve")
        if not response.ok:
            continue
        raw = str(response.data or "")
        if any(marker in raw.casefold() for marker in markers):
            continue
        text = _text(raw)
        # A citable identity claim must contain CIN and enough surrounding context to show the value.
        found = re.search(r"\bCIN\s*[:#-]?\s*([A-Z0-9]{15,21})\b", text, re.I)
        if not found:
            continue
        start = max(0, found.start() - int(cfg.passage_window))
        end = min(len(text), found.end() + int(cfg.passage_window))
        passage = text[start:end].strip()
        cin = found.group(1).upper()
        retrieved_at = response.retrieved_at or ctx.clock()
        publication = _publication_date(text)
        record_ref = url
        source_id = hashlib.sha256(f"{url}|{passage}".encode()).hexdigest()[:20]
        source = Source(source_id=source_id, provider="company_website_claim", record_ref=record_ref,
                        fact="company.cin_claim", value=cin, field_or_passage=passage,
                        record_date=publication,
                        record_date_basis=DateBasis.PUBLICATION_DATE if publication else DateBasis.UNKNOWN,
                        retrieved_at=retrieved_at,
                        limitations=["Self-published website claim; not registry verification"])
        return SimpleNamespace(status="WEBSITE_IDENTITY_CLAIM_FOUND", domain=host, cin=cin,
                               legal_name=None, passage=passage, evidence=source)
    return None
