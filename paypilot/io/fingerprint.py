"""Account fingerprinting at load. The raw number never leaves io/ (except at payout time, A9). Owner: lane A."""
from __future__ import annotations

from paypilot.core.ids import fingerprint_account


def fingerprint(raw: str | None, hmac_key: str) -> tuple[str | None, str | None]:
    """Returns (hmac_fingerprint, last4); (None, None) if blank. Raw value is dropped by the caller."""
    if raw is None or not str(raw).strip():
        return None, None
    s = str(raw).strip()
    alnum = "".join(ch for ch in s if ch.isalnum())
    return fingerprint_account(s, hmac_key), alnum[-4:]
