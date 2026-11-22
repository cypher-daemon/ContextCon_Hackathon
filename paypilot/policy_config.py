"""Strict models for the lane B and C policy sections (C0)."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from paypilot.flags import Flag, NEVER_OVERRIDABLE
from paypilot.schemas import Operation, Proposal, Severity


class _PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Lane B: source settings and identity policy.
class IfscSourceConfig(_PolicyModel):
    enabled: bool
    base_url: str
    retries: int = Field(ge=0)
    backoff_base_s: float = Field(ge=0)
    cache_ttl_h: int = Field(ge=0)


class CrustdataSourceConfig(_PolicyModel):
    base_url: str
    api_version: str
    retries: int = Field(ge=0)
    backoff_base_s: float = Field(ge=0)
    rate_per_min: dict[str, int]
    identify_by: Literal["domain"]
    cache_ttl_h: int = Field(ge=0)
    enabled: bool


class WebfetchSourceConfig(_PolicyModel):
    paths: list[str]
    skip_domains: list[str]
    cf_markers: list[str]
    passage_window: int = Field(ge=0)
    cache_ttl_h: int = Field(ge=0)
    enabled: bool


class SourcesConfig(_PolicyModel):
    ifsc: IfscSourceConfig
    crustdata: CrustdataSourceConfig
    webfetch: WebfetchSourceConfig


class NormalizePolicy(_PolicyModel):
    processor_prefixes: list[str]
    city_tokens: list[str]
    legal_suffixes: list[str]
    abbreviations: dict[str, str]
    invoice_noise_tokens: list[str]

    @field_validator("invoice_noise_tokens", mode="before")
    @classmethod
    def preserve_yaml_legacy_no_token(cls, value: object) -> object:
        # PyYAML's YAML 1.1 resolver reads the unquoted token NO as False.
        # Recover that one known token; all other values remain strictly typed.
        if isinstance(value, list):
            return ["NO" if item is False else item for item in value]
        return value


class MatchPolicy(_PolicyModel):
    fuzzy_min_score: float = Field(ge=0, le=100)
    fuzzy_min_margin: float = Field(ge=0, le=100)
    lookalike_floor: float = Field(ge=0, le=100)
    aliases_extra: dict[str, list[str]] = Field(default_factory=dict)


class RequiredFieldsPolicy(_PolicyModel):
    """Record type → operation → required field names."""

    BankLine: dict[Operation, list[str]]
    Invoice: dict[Operation, list[str]]


class CheckName(StrEnum):
    REQUIRED = "required"
    PERIOD = "period"
    DUPLICATE = "duplicate"
    PO = "po"
    ACCOUNT = "account"
    BANK = "bank"


class PeriodPolicy(_PolicyModel):
    enforce: bool = True


class PostingPolicy(_PolicyModel):
    allowed_directions: list[Literal["DEBIT", "CREDIT"]]
    allowed_currencies: list[str]


class DuplicateHistory(StrEnum):
    THIS_RUN = "this_run"
    DB_HISTORY = "db_history"
    PAYMENT_HISTORY = "payment_history"


class DuplicatePolicy(_PolicyModel):
    window_days: int = Field(ge=0)
    compare_against: list[DuplicateHistory]


class PurchaseOrderPolicy(_PolicyModel):
    required_for: list[Operation]
    tolerance_pct: float = Field(ge=0)
    tolerance_abs_paise: int = Field(ge=0)
    qty_tolerance_pct: float = Field(ge=0)


class BankPolicy(_PolicyModel):
    verify_master_ifsc_before_pay: bool = True


class AccountsPolicy(_PolicyModel):
    by_category: dict[str, str] = Field(default_factory=dict)


class ReconcilePolicy(_PolicyModel):
    window_days: int = Field(ge=0)
    amount_tolerance_paise: int = Field(ge=0)
    settlement_pending_days: int = Field(ge=0)


class DecisionRule(_PolicyModel):
    id: str = Field(min_length=1)
    flags_any: list[Flag] = Field(min_length=1)
    proposal: Proposal
    reason: str = Field(min_length=1)
    missing: str | None = None
    overridable: bool = False


class DecisionPolicy(_PolicyModel):
    rules: list[DecisionRule] = Field(min_length=1)

    @model_validator(mode="after")
    def every_flag_has_one_rule(self) -> "DecisionPolicy":
        covered: dict[Flag, int] = {}
        rule_ids: set[str] = set()
        for rule in self.rules:
            if rule.id in rule_ids:
                raise ValueError(f"duplicate decision rule id: {rule.id}")
            rule_ids.add(rule.id)
            if len(rule.flags_any) != len(set(rule.flags_any)):
                raise ValueError(f"decision rule {rule.id} repeats a flag")
            unsafe_flags = set(rule.flags_any).intersection(NEVER_OVERRIDABLE)
            if rule.overridable and unsafe_flags:
                unsafe = sorted(flag.value for flag in unsafe_flags)
                raise ValueError(
                    f"decision rule {rule.id} makes never-overridable flags overridable: {', '.join(unsafe)}"
                )
            for flag in rule.flags_any:
                covered[flag] = covered.get(flag, 0) + 1

        required = set(Flag) - {Flag.PERSISTENCE_FAILURE}
        missing = sorted(flag.value for flag in required if covered.get(flag, 0) == 0)
        duplicate = sorted(flag.value for flag in required if covered.get(flag, 0) > 1)
        unexpected = sorted(flag.value for flag in covered if flag not in required)
        problems = []
        if missing:
            problems.append(f"uncovered flags: {', '.join(missing)}")
        if duplicate:
            problems.append(f"flags covered more than once: {', '.join(duplicate)}")
        if unexpected:
            problems.append(f"flags that must not have decision rules: {', '.join(unexpected)}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class ExceptionMapping(_PolicyModel):
    type: str = Field(min_length=1)
    severity_pre: Severity
    severity_post: Severity
    blocking: bool
    recovery_case: bool


class ExceptionsPolicy(_PolicyModel):
    map: dict[Flag, ExceptionMapping] = Field(default_factory=dict)


class PolicyConfig(_PolicyModel):
    """Validated B/C configuration plus the runtime decision-hash input."""

    sources: SourcesConfig
    normalize: NormalizePolicy
    match: MatchPolicy
    required_fields: RequiredFieldsPolicy
    checks_order: list[CheckName]
    period: PeriodPolicy
    posting: PostingPolicy
    duplicate: DuplicatePolicy
    po: PurchaseOrderPolicy
    bank: BankPolicy
    accounts: AccountsPolicy
    reconcile: ReconcilePolicy
    decision: DecisionPolicy
    exceptions: ExceptionsPolicy
    decision_hash_input: str = Field(default="", exclude=True)

    def policy_hash_payload(self) -> dict[str, object]:
        """Return only the sections marked as policy in config.yaml.

        ``sources`` is validated here for a shared typed B/C config, but it is
        operational configuration and must not stale decisions when changed.
        """
        return self.model_dump(mode="json", exclude={"sources", "decision_hash_input"})

    @model_validator(mode="after")
    def check_order_is_complete_and_unique(self) -> "PolicyConfig":
        if len(self.checks_order) != len(set(self.checks_order)):
            raise ValueError("checks_order must not contain duplicate checks")
        if set(self.checks_order) != set(CheckName):
            raise ValueError("checks_order must include each supported check exactly once")
        return self


__all__ = ["PolicyConfig", "DecisionPolicy", "DecisionRule", "ExceptionMapping"]
