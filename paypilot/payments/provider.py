"""Provider plumbing: shared errors and the factory. Adapters: payments/fake.py (A8), payments/razorpayx_test.py (A9). Owner: lane A.

There are exactly two providers, `fake` and `razorpayx_test`. There is no live option and no fallback between them.
"""
from __future__ import annotations

from paypilot.schemas import RunConfig

PROVIDER_NAMES = ("fake", "razorpayx_test")


class ProviderTimeout(Exception):
    """The provider call may or may not have reached the provider -> PAYMENT_UNCERTAIN, reconcile with the SAME key."""


class ProviderRejected(Exception):
    """The provider refused the request itself (e.g. a replayed key with a different body) -> PAYMENT_UNCERTAIN, never a new key."""


def build_provider(name: str, cfg: RunConfig):
    """Build the adapter for `name`. Anything else is refused (no live option, no silent fallback)."""
    if name == "fake":
        from paypilot.payments.fake import FakeProvider
        return FakeProvider(cfg.storage["fake_provider_db"])
    if name == "razorpayx_test":
        from paypilot.payments.razorpayx_test import RazorpayXTestProvider
        return RazorpayXTestProvider.from_env(cfg)
    raise ValueError(f"unknown payment provider {name!r}; allowed: {list(PROVIDER_NAMES)}")
