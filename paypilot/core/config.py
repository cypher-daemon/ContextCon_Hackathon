"""Two-layer config: config.yaml defaults + clients/<id>/policy.yaml overrides (policy sections only). Owner: lane A.

Strict: unknown keys, overrides of runtime sections, or an invalid provider are errors (exit 2).
`policy_hash` covers only the [policy] sections; with `data_hash` it forms `decision_hash_input`.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from paypilot.core.ids import canonical_json, sha256_hex
from paypilot.policy_config import PolicyConfig
from paypilot.schemas import RunConfig

RUNTIME_SECTIONS = ("limits", "payments", "approvals", "costs", "output", "logging", "storage")
POLICY_SECTIONS = ("normalize", "match", "required_fields", "checks_order", "period", "posting", "duplicate", "po",
                   "bank", "accounts", "reconcile", "decision", "exceptions")
OPERATIONAL_SECTIONS = ("sources",)                     # B's source settings: validated, not overridable per client
ALLOWED_PROVIDERS = ("fake", "razorpayx_test")          # no live option exists


class ConfigError(Exception):
    """Invalid configuration -> CLI exit 2."""


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


CLIENT_RUNTIME_KEYS = {"approvals": {"approvers"}}      # the one runtime key a client sets (INTERFACES §7: the approver allowlist)


def merge_config(defaults: dict, client_policy: dict | None) -> dict:
    """Deep-merge; a client may override POLICY sections, plus `approvals.approvers` (the allowlist). Nothing else."""
    client_policy = client_policy or {}
    bad = sorted(set(client_policy) - set(POLICY_SECTIONS) - set(CLIENT_RUNTIME_KEYS))
    for section, allowed in CLIENT_RUNTIME_KEYS.items():
        extra = set(client_policy.get(section) or {}) - allowed
        if extra:
            bad.append(f"{section}.{sorted(extra)}")
    if bad:
        raise ConfigError(f"policy.yaml may only override policy sections and approvals.approvers; not allowed: {bad}")
    return _deep_merge(defaults, client_policy)


def policy_hash_of(policy: PolicyConfig) -> str:
    return sha256_hex(canonical_json(policy.policy_hash_payload()))


def decision_hash_input(policy_hash: str, data_hash: str) -> str:
    return sha256_hex(policy_hash, data_hash)


def with_data_hash(policy: PolicyConfig, data_hash: str) -> PolicyConfig:
    """Bind the loaded client data into decision_hash_input (policy_hash + data_hash)."""
    return policy.model_copy(update={"decision_hash_input": decision_hash_input(policy_hash_of(policy), data_hash)})


def load_config(client_id: str, *, root: Path = Path("."), config_path: Path | None = None,
                client_policy_path: Path | None = None) -> tuple[RunConfig, PolicyConfig, dict]:
    """Returns (RunConfig, PolicyConfig, merged_snapshot). The snapshot is stored in runs.policy_snapshot_json."""
    defaults = _read_yaml(config_path or root / "config.yaml")
    policy_file = client_policy_path or root / "clients" / client_id / "policy.yaml"
    merged = merge_config(defaults, _read_yaml(policy_file) if policy_file.exists() else None)

    known = set(RUNTIME_SECTIONS) | set(POLICY_SECTIONS) | set(OPERATIONAL_SECTIONS)
    if set(merged) - known:
        raise ConfigError(f"unknown config sections: {sorted(set(merged) - known)}")
    missing = known - set(merged)
    if missing:
        raise ConfigError(f"missing config sections: {sorted(missing)}")
    provider = merged["payments"].get("provider")
    if provider not in ALLOWED_PROVIDERS:
        raise ConfigError(f"payments.provider must be one of {ALLOWED_PROVIDERS}, got {provider!r} (no live option exists)")

    try:
        policy = PolicyConfig(**{k: merged[k] for k in (*OPERATIONAL_SECTIONS, *POLICY_SECTIONS)})
    except ValidationError as e:
        raise ConfigError(f"invalid policy/source config: {e}") from e

    phash = policy_hash_of(policy)
    runtime = {k: merged[k] for k in RUNTIME_SECTIONS}
    chash = sha256_hex(canonical_json({**runtime, "sources": merged["sources"]}), phash)
    cfg = RunConfig(**runtime, sources=merged["sources"], config_hash=chash, policy_hash=phash)
    return cfg, policy, merged
