from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

SplitName = Literal["development", "test"]


@dataclass(frozen=True, slots=True)
class CandidateCase:
    """The complete, label-blind input allowed to influence corpus selection."""

    example_id: str
    task_id: str
    trial: int
    packet_length: int

    def __post_init__(self) -> None:
        if not self.example_id.strip():
            raise ValueError("example_id must not be blank")
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if self.trial < 0:
            raise ValueError("trial must be nonnegative")
        if self.packet_length < 0:
            raise ValueError("packet_length must be nonnegative")


@dataclass(frozen=True, slots=True)
class SelectedCase:
    example_id: str
    task_id: str
    task_family_id: str
    trial: int
    packet_length: int
    split: SplitName
    pool: Literal["model_judgment", "annotation_calibration"]


@dataclass(frozen=True, slots=True)
class PoolSelection:
    model_judgment: tuple[SelectedCase, ...]
    annotation_calibration: tuple[SelectedCase, ...]


# Manually reviewed against the visible task scenarios in the G0-approved retail
# source. A group contains cross-ID variants of the same underlying scenario.
# Sharing only a customer identity, product category, or broad action is not
# sufficient. IDs not listed here are reviewed singletons.
REVIEWED_RETAIL_VARIANT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("0", "1"),
    ("3", "4"),
    ("5", "6", "7", "8", "9"),
    ("10", "11"),
    ("12", "13", "14"),
    ("23", "70"),
    ("25", "26"),
    ("27", "28", "29"),
    ("30", "31", "32"),
    ("33", "34"),
    ("36", "37", "38"),
    ("41", "42"),
    ("45", "46", "47", "48"),
    ("51", "52"),
    ("54", "55"),
    ("56", "57"),
    ("60", "61"),
    ("62", "63"),
    ("67", "68"),
    ("71", "72"),
    ("82", "83", "84"),
    ("85", "86", "87"),
    ("91", "92"),
    ("93", "94", "95"),
    ("96", "97"),
    ("98", "99"),
    ("101", "102"),
    ("103", "104"),
    ("109", "110"),
    ("111", "112"),
)
RETAIL_TASK_IDS = frozenset(str(task_id) for task_id in range(114))


def derive_retail_task_families(task_ids: Iterable[str]) -> dict[str, str]:
    """Return the frozen reviewed family mapping for the approved task frame.

    The function accepts identifiers only, making labels, benchmark rewards,
    expected actions, and judge outputs structurally unavailable to the runtime
    derivation. The approved raw-artifact hash separately binds these IDs to the
    task definitions that were reviewed to create the variant map above.
    """

    ids = [str(task_id) for task_id in task_ids]
    if len(ids) != len(set(ids)):
        raise ValueError("task IDs must be unique")
    supplied = set(ids)
    if supplied != RETAIL_TASK_IDS:
        missing = sorted(RETAIL_TASK_IDS - supplied, key=int)
        extra = sorted(supplied - RETAIL_TASK_IDS)
        raise ValueError(f"retail task frame mismatch; missing={missing}, extra={extra}")

    grouped_ids = {task_id for group in REVIEWED_RETAIL_VARIANT_GROUPS for task_id in group}
    if len(grouped_ids) != sum(map(len, REVIEWED_RETAIL_VARIANT_GROUPS)):
        raise AssertionError("reviewed retail variant groups overlap")

    groups = [*REVIEWED_RETAIL_VARIANT_GROUPS]
    groups.extend((task_id,) for task_id in sorted(RETAIL_TASK_IDS - grouped_ids, key=int))
    mapping: dict[str, str] = {}
    for group in groups:
        anchor = min(map(int, group))
        family_id = f"retail-task-family-{anchor:03d}"
        mapping.update(dict.fromkeys(group, family_id))
    return dict(sorted(mapping.items(), key=lambda item: int(item[0])))


def assign_grouped_split(
    family_ids: Iterable[str],
    *,
    development_fraction: float,
    seed: int,
) -> dict[str, SplitName]:
    """Assign complete families to a deterministic exact-size grouped split."""

    families = sorted(set(family_ids))
    if len(families) < 2:
        raise ValueError("at least two task families are required")
    if not 0 < development_fraction < 1:
        raise ValueError("development_fraction must be between zero and one")

    development_count = round(len(families) * development_fraction)
    development_count = max(1, min(len(families) - 1, development_count))
    ranked = sorted(families, key=lambda family_id: _hash_rank(seed, "split", family_id))
    development = set(ranked[:development_count])
    return {
        family_id: "development" if family_id in development else "test"
        for family_id in families
    }


def select_label_blind_pools(
    cases: Sequence[CandidateCase],
    *,
    family_by_task: Mapping[str, str],
    split_by_family: Mapping[str, SplitName],
    seed: int,
    model_judgment_cases: int = 21,
    model_development_cases: int = 5,
    calibration_cases: int = 24,
    calibration_minimum_families: int = 12,
) -> PoolSelection:
    """Select disjoint study and calibration pools using only IDs and lengths.

    One model-judgment case is selected per family. Calibration cases come only
    from otherwise unselected development families. Empirical packet-length
    quartiles are interleaved before the seeded tie-break, avoiding a sample
    made entirely of unusually short or long packets without inspecting any
    outcomes.
    """

    _validate_selection_inputs(
        cases,
        family_by_task=family_by_task,
        split_by_family=split_by_family,
        model_judgment_cases=model_judgment_cases,
        model_development_cases=model_development_cases,
        calibration_cases=calibration_cases,
        calibration_minimum_families=calibration_minimum_families,
    )

    by_family: dict[str, list[CandidateCase]] = defaultdict(list)
    for case in cases:
        by_family[family_by_task[case.task_id]].append(case)

    length_bins = _empirical_length_quartiles(cases)
    representatives = [
        min(
            family_cases,
            key=lambda case: _hash_rank(seed, "study-representative", case.example_id),
        )
        for family_cases in by_family.values()
    ]
    representatives_by_split: dict[SplitName, list[CandidateCase]] = {
        "development": [],
        "test": [],
    }
    for case in representatives:
        family_id = family_by_task[case.task_id]
        representatives_by_split[split_by_family[family_id]].append(case)

    requested_by_split = {
        "development": model_development_cases,
        "test": model_judgment_cases - model_development_cases,
    }
    study_cases: list[CandidateCase] = []
    for split, requested in requested_by_split.items():
        ordered = _length_balanced_order(
            representatives_by_split[split],
            length_bins=length_bins,
            seed=seed,
            namespace=f"study-{split}",
        )
        if len(ordered) < requested:
            raise ValueError(f"not enough {split} families for {requested} study cases")
        study_cases.extend(ordered[:requested])

    study_families = {family_by_task[case.task_id] for case in study_cases}
    calibration_families = [
        family_id
        for family_id in by_family
        if split_by_family[family_id] == "development" and family_id not in study_families
    ]
    calibration_representatives = [
        min(
            by_family[family_id],
            key=lambda case: _hash_rank(seed, "calibration-representative", case.example_id),
        )
        for family_id in calibration_families
    ]
    ordered_calibration_representatives = _length_balanced_order(
        calibration_representatives,
        length_bins=length_bins,
        seed=seed,
        namespace="calibration-family",
    )
    if len(ordered_calibration_representatives) < calibration_minimum_families:
        raise ValueError(
            "not enough otherwise-unselected development families for calibration"
        )
    chosen_calibration_families = [
        family_by_task[case.task_id]
        for case in ordered_calibration_representatives[:calibration_minimum_families]
    ]
    calibration_selected = _round_robin_family_cases(
        chosen_calibration_families,
        by_family=by_family,
        count=calibration_cases,
        seed=seed,
    )

    model_selected = tuple(
        _selected_case(
            case,
            family_by_task=family_by_task,
            split_by_family=split_by_family,
            pool="model_judgment",
        )
        for case in sorted(
            study_cases,
            key=lambda case: _hash_rank(seed, "study-output-order", case.example_id),
        )
    )
    calibration_output = tuple(
        _selected_case(
            case,
            family_by_task=family_by_task,
            split_by_family=split_by_family,
            pool="annotation_calibration",
        )
        for case in calibration_selected
    )
    _validate_pool_disjointness(model_selected, calibration_output)
    return PoolSelection(
        model_judgment=model_selected,
        annotation_calibration=calibration_output,
    )


def _validate_selection_inputs(
    cases: Sequence[CandidateCase],
    *,
    family_by_task: Mapping[str, str],
    split_by_family: Mapping[str, SplitName],
    model_judgment_cases: int,
    model_development_cases: int,
    calibration_cases: int,
    calibration_minimum_families: int,
) -> None:
    if not cases:
        raise ValueError("candidate cases must not be empty")
    example_ids = [case.example_id for case in cases]
    if len(example_ids) != len(set(example_ids)):
        raise ValueError("candidate example IDs must be unique")
    task_trials = [(case.task_id, case.trial) for case in cases]
    if len(task_trials) != len(set(task_trials)):
        raise ValueError("candidate task/trial pairs must be unique")
    missing_tasks = sorted({case.task_id for case in cases} - set(family_by_task))
    if missing_tasks:
        raise ValueError(f"candidate tasks have no family: {missing_tasks}")
    used_families = {family_by_task[case.task_id] for case in cases}
    missing_splits = sorted(used_families - set(split_by_family))
    if missing_splits:
        raise ValueError(f"candidate families have no split: {missing_splits}")
    invalid_splits = sorted(
        family_id
        for family_id in used_families
        if split_by_family[family_id] not in {"development", "test"}
    )
    if invalid_splits:
        raise ValueError(f"candidate families have invalid splits: {invalid_splits}")
    if model_judgment_cases < 1:
        raise ValueError("model_judgment_cases must be positive")
    if not 0 <= model_development_cases <= model_judgment_cases:
        raise ValueError("model_development_cases must be within the study size")
    if calibration_minimum_families < 1:
        raise ValueError("calibration_minimum_families must be positive")
    if calibration_cases < calibration_minimum_families:
        raise ValueError("calibration cases cannot span the requested minimum families")


def _empirical_length_quartiles(cases: Sequence[CandidateCase]) -> dict[str, int]:
    ordered = sorted(cases, key=lambda case: (case.packet_length, case.example_id))
    count = len(ordered)
    return {
        case.example_id: min(3, index * 4 // count) for index, case in enumerate(ordered)
    }


def _length_balanced_order(
    cases: Sequence[CandidateCase],
    *,
    length_bins: Mapping[str, int],
    seed: int,
    namespace: str,
) -> list[CandidateCase]:
    bins: dict[int, list[CandidateCase]] = {index: [] for index in range(4)}
    for case in cases:
        bins[length_bins[case.example_id]].append(case)
    for bin_index, values in bins.items():
        values.sort(
            key=lambda case: _hash_rank(
                seed, f"{namespace}-length-bin-{bin_index}", case.example_id
            )
        )

    ordered: list[CandidateCase] = []
    offset = 0
    while len(ordered) < len(cases):
        for bin_index in range(4):
            if offset < len(bins[bin_index]):
                ordered.append(bins[bin_index][offset])
        offset += 1
    return ordered


def _round_robin_family_cases(
    family_ids: Sequence[str],
    *,
    by_family: Mapping[str, Sequence[CandidateCase]],
    count: int,
    seed: int,
) -> tuple[CandidateCase, ...]:
    ordered_by_family = {
        family_id: sorted(
            by_family[family_id],
            key=lambda case: _hash_rank(
                seed, f"calibration-case-{family_id}", case.example_id
            ),
        )
        for family_id in family_ids
    }
    selected: list[CandidateCase] = []
    offset = 0
    while len(selected) < count:
        added = False
        for family_id in family_ids:
            values = ordered_by_family[family_id]
            if offset < len(values):
                selected.append(values[offset])
                added = True
                if len(selected) == count:
                    return tuple(selected)
        if not added:
            raise ValueError("not enough cases in the chosen calibration families")
        offset += 1
    return tuple(selected)


def _selected_case(
    case: CandidateCase,
    *,
    family_by_task: Mapping[str, str],
    split_by_family: Mapping[str, SplitName],
    pool: Literal["model_judgment", "annotation_calibration"],
) -> SelectedCase:
    family_id = family_by_task[case.task_id]
    return SelectedCase(
        example_id=case.example_id,
        task_id=case.task_id,
        task_family_id=family_id,
        trial=case.trial,
        packet_length=case.packet_length,
        split=split_by_family[family_id],
        pool=pool,
    )


def _validate_pool_disjointness(
    model_judgment: Sequence[SelectedCase],
    calibration: Sequence[SelectedCase],
) -> None:
    model_ids = {case.example_id for case in model_judgment}
    calibration_ids = {case.example_id for case in calibration}
    if model_ids & calibration_ids:
        raise AssertionError("study and calibration cases overlap")
    model_families = {case.task_family_id for case in model_judgment}
    calibration_families = {case.task_family_id for case in calibration}
    if model_families & calibration_families:
        raise AssertionError("study and calibration task families overlap")
    if any(case.split != "development" for case in calibration):
        raise AssertionError("calibration cases must all be development cases")


def _hash_rank(seed: int, namespace: str, value: str) -> bytes:
    material = f"{seed}\0{namespace}\0{value}".encode()
    return hashlib.sha256(material).digest()
