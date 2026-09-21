from pathlib import Path

import yaml

from judge_compare.hashing import sha256_file

ROOT = Path(__file__).resolve().parents[1]


def _decision() -> dict:
    with (ROOT / "reviews" / "G0-decision.yaml").open(
        "r", encoding="utf-8"
    ) as handle:
        return yaml.safe_load(handle)


def test_g0_decision_binds_the_reviewed_artifacts() -> None:
    decision = _decision()

    assert decision["decision"] == "approved"
    assert decision["reviewer"] == "Ayush"
    assert decision["approved_budget_usd"] == 30.0
    assert decision["conditions"] == []

    for relative_path, expected_hash in decision["artifact_hashes"].items():
        assert sha256_file(ROOT / relative_path) == expected_hash


def test_active_config_is_exactly_the_approved_proposal() -> None:
    decision = _decision()
    active = ROOT / "configs" / "experiment.yaml"
    proposal = ROOT / "configs" / "g0-gpt54.yaml"

    assert active.read_bytes() == proposal.read_bytes()
    assert sha256_file(active) == decision["artifact_hashes"][
        "configs/g0-gpt54.yaml"
    ]


def test_g0_does_not_authorize_paid_execution() -> None:
    scope = _decision()["approved_scope"]

    assert scope["model_judgment_cases_maximum"] == 21
    assert scope["paid_request_attempts_maximum"] == 126
    assert scope["paid_development_execution_authorized"] is False
    assert scope["paid_holdout_execution_authorized"] is False
    assert scope["repeatability_enabled"] is False
    assert scope["annotation_calibration"]["cases"] == 24
    assert scope["annotation_calibration"]["minimum_task_families"] == 12
    assert scope["annotation_calibration"]["scope"] == (
        "annotation_only_no_judge_calls"
    )
    assert scope["annotation_calibration"][
        "case_overlap_with_model_judgment_sample"
    ] is False
    assert scope["annotation_calibration"][
        "task_family_overlap_with_model_judgment_sample"
    ] is False
