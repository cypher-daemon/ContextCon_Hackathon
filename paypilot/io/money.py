"""Exact money parsing. Decimal strings only; floats are rejected. Owner: lane A."""
from __future__ import annotations

import re
from decimal import Decimal

_AMOUNT = re.compile(r"^-?\d{1,15}(\.\d{1,2})?$")


def parse_amount_paise(value: object) -> int:
    """'1240.00' -> 124000. Rejects numbers (float/int), commas, >2 decimals, blanks. Negative/zero parse (flagged later)."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("amount must be a decimal string like \"1240.00\" (numbers are rejected)")
    s = value.strip()
    if not _AMOUNT.match(s):
        raise ValueError("amount must look like 1240 or 1240.50 (max 2 decimals, no commas)")
    return int((Decimal(s) * 100).to_integral_exact())
