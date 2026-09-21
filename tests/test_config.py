from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from judge_compare.config import ExperimentConfig, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_repository_configuration_matches_approved_g0_scope() -> None:
    config = load_config(ROOT / "configs" / "experiment.yaml")

    assert config.protocol_revision == 2
    assert config.execution.maximum_attempts == 3
    assert config.execution.maximum_cases == 21
    assert config.execution.maximum_paid_requests == 126
    assert config.execution.budget_usd == 30.0
    assert config.recording.local_artifacts_required is True
    assert config.judges.frontier.model == "gpt-5.4-2026-03-05"
    assert config.repeatability.enabled is False
    assert config.pending_review_paths() == []
    config.assert_g0_resolved()


def test_g0_guard_rejects_a_synthetic_unresolved_configuration() -> None:
    with (ROOT / "configs" / "experiment.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        raw = yaml.safe_load(handle)
    raw["judges"]["frontier"]["model"] = "pending_g0"
    config = ExperimentConfig.model_validate(raw)

    with pytest.raises(ValueError, match="G0 configuration is unresolved"):
        config.assert_g0_resolved()


def test_arbitrary_string_cannot_bypass_numeric_g0_field() -> None:
    with (ROOT / "configs" / "experiment.yaml").open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    raw["execution"]["timeout_seconds"] = "banana"

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(raw)


def test_astra_proposal_is_fully_resolved() -> None:
    config = load_config(ROOT / "configs" / "g0-astra.yaml")

    config.assert_g0_resolved()
    assert config.pending_review_paths() == []
    assert config.execution.maximum_cases == 456
    assert config.repeatability.target_packets == 15


def test_budget_limited_gpt54_proposal_is_fully_resolved() -> None:
    config = load_config(ROOT / "configs" / "g0-gpt54.yaml")

    config.assert_g0_resolved()
    assert config.pending_review_paths() == []
    assert config.execution.maximum_cases == 21
    assert config.execution.maximum_paid_requests == 126
    assert config.execution.budget_usd == 30.0
    assert config.repeatability.enabled is False
    assert config.repeatability.target_packets == 0
    assert config.repeatability.additional_budget_usd == 0.0

    frontier_attempt = Decimal("32000") * Decimal("2.50") / Decimal(
        "1000000"
    ) + Decimal("25000") * Decimal("15") / Decimal("1000000")
    jev_attempt = Decimal("64000") * Decimal("0.042") / Decimal("1000000")
    case_envelope = Decimal(config.execution.maximum_attempts) * (
        frontier_attempt + jev_attempt
    )
    maximum_cases = int(Decimal(str(config.execution.budget_usd)) // case_envelope)

    assert frontier_attempt == Decimal("0.455")
    assert jev_attempt == Decimal("0.002688")
    assert case_envelope == Decimal("1.373064")
    assert maximum_cases == config.execution.maximum_cases == 21
    assert config.execution.maximum_paid_requests == (
        config.execution.maximum_cases * 2 * config.execution.maximum_attempts
    )


def test_disabled_repeatability_rejects_hidden_repeat_calls() -> None:
    with (ROOT / "configs" / "g0-gpt54.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        raw = yaml.safe_load(handle)
    raw["repeatability"]["target_packets"] = 1

    with pytest.raises(ValidationError, match="disabled repeatability"):
        ExperimentConfig.model_validate(raw)
