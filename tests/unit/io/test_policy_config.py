from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from paypilot.flags import Flag, NEVER_OVERRIDABLE
from paypilot.policy_config import PolicyConfig


@pytest.fixture
def config_data() -> dict:
    full_config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    policy_sections = {
        "sources",
        "normalize",
        "match",
        "required_fields",
        "checks_order",
        "period",
        "posting",
        "duplicate",
        "po",
        "bank",
        "accounts",
        "reconcile",
        "decision",
        "exceptions",
    }
    return {key: value for key, value in full_config.items() if key in policy_sections}


def test_product_policy_config_validates(config_data: dict) -> None:
    policy = PolicyConfig.model_validate(config_data)

    assert policy.normalize.processor_prefixes
    assert policy.sources.crustdata.identify_by == "domain"
    assert policy.checks_order[0] == "required"
    assert len(policy.decision.rules) > 0
    assert "sources" not in policy.policy_hash_payload()
    assert "decision" in policy.policy_hash_payload()


def test_every_decision_flag_is_covered_exactly_once(config_data: dict) -> None:
    invalid = deepcopy(config_data)
    invalid["decision"]["rules"][4]["flags_any"].remove("ALREADY_PAID")

    with pytest.raises(ValidationError, match="uncovered flags: ALREADY_PAID"):
        PolicyConfig.model_validate(invalid)


def test_a_decision_flag_cannot_be_covered_by_two_rules(config_data: dict) -> None:
    invalid = deepcopy(config_data)
    invalid["decision"]["rules"][1]["flags_any"].append("DUPLICATE_INVOICE")

    with pytest.raises(ValidationError, match="flags covered more than once: DUPLICATE_INVOICE"):
        PolicyConfig.model_validate(invalid)


def test_never_overridable_flags_cannot_be_made_overridable(config_data: dict) -> None:
    invalid = deepcopy(config_data)
    invalid["decision"]["rules"][0]["overridable"] = True

    with pytest.raises(ValidationError, match="never-overridable flags"):
        PolicyConfig.model_validate(invalid)


def test_policy_models_reject_unknown_fields(config_data: dict) -> None:
    invalid = deepcopy(config_data)
    invalid["duplicate"]["guessing_is_ok"] = True

    with pytest.raises(ValidationError, match="guessing_is_ok"):
        PolicyConfig.model_validate(invalid)


def test_persistence_failure_is_never_overridable() -> None:
    assert Flag.PERSISTENCE_FAILURE in NEVER_OVERRIDABLE
