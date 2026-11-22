"""Crash points for the kill tests (FAILURE_MODES §3). Owner: lane A.

PAYPILOT_CRASH_AT=<point> -> os._exit(137) at that point: no cleanup, no flush, like kill -9.
Place `crash_point("...")` right AFTER the durable step it names, so recovery is exercised on the persisted state.
"""
from __future__ import annotations

import os

CRASH_POINTS = (
    "after_ingest", "after_reserve", "after_ext_response_before_cache", "after_resolve", "after_check", "after_decide",
    "after_post_txn", "after_approval_commit", "after_payment_insert", "after_create_sent",
    "after_create_response_before_ref", "after_ref_saved", "after_confirm_sent",
    "after_confirm_response_before_commit", "after_paid_commit",
)
ENV = "PAYPILOT_CRASH_AT"
EXIT_KILLED = 137


def configured_point() -> str | None:
    return os.environ.get(ENV) or None


def validate_env() -> None:
    """Call once at CLI start: a typo in the env var must not silently disable a crash test."""
    p = configured_point()
    if p is not None and p not in CRASH_POINTS:
        raise ValueError(f"{ENV}={p!r} is not a known crash point; valid: {', '.join(CRASH_POINTS)}")


def crash_point(name: str) -> None:
    if name not in CRASH_POINTS:
        raise ValueError(f"unknown crash point {name!r}")
    if configured_point() == name:
        os._exit(EXIT_KILLED)
