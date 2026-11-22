"""Validated access to the declarative decision rules (C6)."""
from __future__ import annotations

from typing import Any

from paypilot.flags import Flag, NEVER_OVERRIDABLE
from paypilot.policy_config import DecisionPolicy, DecisionRule


def load_rules(policy: Any) -> list[DecisionRule]:
    """Return policy rules after enforcing complete, unique, safe flag coverage."""
    section = getattr(policy, "decision", policy)
    rules = getattr(section, "rules", None)
    if not isinstance(rules, list) or not rules:
        raise ValueError("decision policy must contain at least one rule")
    validated = DecisionPolicy(rules=rules).rules
    covered: dict[Flag, str] = {}
    for rule in validated:
        for flag in rule.flags_any:
            if flag in covered:
                raise ValueError(f"flag {flag.value} is covered by both {covered[flag]} and {rule.id}")
            if rule.overridable and flag in NEVER_OVERRIDABLE:
                raise ValueError(f"rule {rule.id} cannot make {flag.value} overridable")
            covered[flag] = rule.id
    required = set(Flag) - {Flag.PERSISTENCE_FAILURE}
    missing = sorted(flag.value for flag in required - covered.keys())
    unexpected = sorted(flag.value for flag in covered.keys() - required)
    if missing or unexpected:
        raise ValueError(f"decision rules must cover each decision flag once; missing={missing}, unexpected={unexpected}")
    return validated
