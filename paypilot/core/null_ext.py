"""ExtClient used until lane B's sources/http.py is wired over the ledger (A6). Every call fails closed. Owner: lane A.

Unavailable is never a PASS: callers see ok=False / BLOCKED and must emit SOURCE_UNAVAILABLE rather than guess.
"""
from __future__ import annotations

from datetime import datetime, timezone

from paypilot.schemas import ExtResponse


class NullExtClient:
    def _blocked(self) -> ExtResponse:
        return ExtResponse(ok=False, status=None, error="BLOCKED", retrieved_at=datetime.now(timezone.utc))

    def get_json(self, provider, operation, url, *, params=None, item_id=None, stage=""):
        return self._blocked()

    def post_json(self, provider, operation, url, body, *, item_id=None, stage=""):
        return self._blocked()

    def fetch_page(self, url, *, item_id=None, stage=""):
        return self._blocked()
