from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from judge_compare.corpus_selection import (
    CandidateCase,
    assign_grouped_split,
    derive_retail_task_families,
    select_label_blind_pools,
)

ROOT = Path(__file__).resolve().parents[1]
RAW_SOURCE = (
    ROOT
    / "data/raw/tau2/claude-sonnet-4-5_sierra_2026-02-26"
    / "claude-sonnet-4-5_enabled_retail_gpt-5.2_4trials.json"
)


def _approved_candidates() -> tuple[list[str], list[CandidateCase]]:
    data = json.loads(RAW_SOURCE.read_text(encoding="utf-8"))
    task_ids = [str(task["id"]) for task in data["tasks"]]
    cases: list[CandidateCase] = []
    for simulation in data["simulations"]:
        target = next(
            message
            for message in reversed(simulation["messages"])
            if message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        )
        cases.append(
            CandidateCase(
                example_id=str(simulation["id"]),
                task_id=str(simulation["task_id"]),
                trial=int(simulation["trial"]),
                packet_length=int(target["usage"]["prompt_tokens"]),
            )
        )
    return task_ids, cases


def test_reviewed_family_derivation_covers_approved_frame() -> None:
    task_ids, _ = _approved_candidates()
    mapping = derive_retail_task_families(task_ids)

    assert len(mapping) == 114
    assert len(set(mapping.values())) == 72
    assert mapping["0"] == mapping["1"]
    assert mapping["23"] == mapping["70"]
    assert mapping["2"] != mapping["3"]  # A shared sub-request is not enough.
    assert mapping["18"] != mapping["19"]  # Same customer/entity is not enough.
    assert mapping["20"] != mapping["21"]  # Same customer/action is not enough.
    assert mapping["44"] != mapping["49"]  # Same customer is not enough.
    assert mapping["78"] != mapping["113"]  # Same name, different identity/scenario.


def test_family_derivation_rejects_a_changed_task_frame() -> None:
    with pytest.raises(ValueError, match="task frame mismatch"):
        derive_retail_task_families([str(task_id) for task_id in range(113)])


def test_grouped_split_and_label_blind_pool_contract() -> None:
    task_ids, candidates = _approved_candidates()
    families = derive_retail_task_families(task_ids)
    split = assign_grouped_split(
        families.values(), development_fraction=0.25, seed=20260920
    )
    selection = select_label_blind_pools(
        candidates,
        family_by_task=families,
        split_by_family=split,
        seed=20260921,
    )

    assert Counter(split.values()) == {"test": 54, "development": 18}
    assert len(selection.model_judgment) == 21
    assert Counter(case.split for case in selection.model_judgment) == {
        "test": 16,
        "development": 5,
    }
    assert len({case.task_family_id for case in selection.model_judgment}) == 21
    assert len(selection.annotation_calibration) == 24
    assert {case.split for case in selection.annotation_calibration} == {"development"}
    assert len({case.task_family_id for case in selection.annotation_calibration}) == 12
    assert not (
        {case.example_id for case in selection.model_judgment}
        & {case.example_id for case in selection.annotation_calibration}
    )
    assert not (
        {case.task_family_id for case in selection.model_judgment}
        & {case.task_family_id for case in selection.annotation_calibration}
    )


def test_selection_is_deterministic_and_seeded() -> None:
    task_ids, candidates = _approved_candidates()
    families = derive_retail_task_families(task_ids)
    split = assign_grouped_split(
        families.values(), development_fraction=0.25, seed=20260920
    )

    first = select_label_blind_pools(
        candidates,
        family_by_task=families,
        split_by_family=split,
        seed=20260921,
    )
    repeated = select_label_blind_pools(
        list(reversed(candidates)),
        family_by_task=dict(reversed(families.items())),
        split_by_family=dict(reversed(split.items())),
        seed=20260921,
    )
    different_seed = select_label_blind_pools(
        candidates,
        family_by_task=families,
        split_by_family=split,
        seed=20260922,
    )

    assert first == repeated
    assert first != different_seed


def test_calibration_capacity_failure_stops_instead_of_using_test_families() -> None:
    cases = [
        CandidateCase(
            example_id=f"case-{family}-{trial}",
            task_id=str(family),
            trial=trial,
            packet_length=100 + family + trial,
        )
        for family in range(8)
        for trial in range(4)
    ]
    family_by_task = {str(family): f"family-{family}" for family in range(8)}
    split = {
        f"family-{family}": "development" if family < 3 else "test"
        for family in range(8)
    }

    with pytest.raises(ValueError, match="otherwise-unselected development families"):
        select_label_blind_pools(
            cases,
            family_by_task=family_by_task,
            split_by_family=split,
            seed=1,
            model_judgment_cases=3,
            model_development_cases=1,
            calibration_cases=4,
            calibration_minimum_families=3,
        )
