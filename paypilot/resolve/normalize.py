"""Deterministic normalization helpers for vendor and payment identifiers."""
from __future__ import annotations

import re
from typing import Any


def _setting(policy: Any, name: str, default: Any) -> Any:
    if isinstance(policy, dict):
        return policy.get(name, default)
    return getattr(policy, name, default)


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Z0-9]+", value.upper())


def _expand_abbreviations(words: list[str], abbreviations: dict[str, str]) -> list[str]:
    mapping = {str(k).upper(): _words(str(v)) for k, v in abbreviations.items()}
    return [part for word in words for part in mapping.get(word, [word])]


def descriptor_key(s: str, policy: Any) -> str:
    """Normalize a bank descriptor by removing configured processor/city noise."""
    text = str(s or "").strip().upper()
    for pattern in _setting(policy, "processor_prefixes", []):
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    words = _expand_abbreviations(_words(text), _setting(policy, "abbreviations", {}))
    city = {str(token).upper() for token in _setting(policy, "city_tokens", [])}
    words = [word for word in words if word not in city]
    return " ".join(words)


def name_key(s: str, policy: Any) -> str:
    """Normalize a vendor name while retaining meaningful entity terms."""
    words = _expand_abbreviations(_words(str(s or "")), _setting(policy, "abbreviations", {}))
    suffixes = {str(token).upper() for token in _setting(policy, "legal_suffixes", [])}
    return " ".join(word for word in words if word not in suffixes)


def invoice_canonical(s: str, policy: Any) -> str:
    """Canonicalize invoice numbers by removing configured noise and separators."""
    noise = {str(token).upper() for token in _setting(policy, "invoice_noise_tokens", [])}
    words = [word for word in _words(str(s or "")) if word not in noise]
    # Numeric components are compared by value so zero-padded segments agree.
    words = [str(int(word)) if word.isdigit() else word for word in words]
    return "|".join(words)
