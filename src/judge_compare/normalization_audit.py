from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from judge_compare.hashing import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
    sha256_file,
)
from judge_compare.io import write_text_atomic
from judge_compare.m1 import (
    HUMAN_AUDIT_INVENTORY_RELATIVE,
    HUMAN_AUDIT_REQUIRED_CHECKS,
    M1_SCHEMA_VERSION,
    PACKET_ROOT_RELATIVE,
    TIMING_EXPOSED_FAMILY,
    M1Preparation,
    M1PreparationError,
    prepare_m1,
)
from judge_compare.models import EvidencePacket
from judge_compare.paths import safe_join

AUDIT_CASE_COUNT = 10
AUDIT_OUTCOME_COUNT = 5
AUDIT_ROOT_NAME = "audits"
AUDIT_REVIEW_AID_NAME = "M1-normalization-audit-review-aid.json"
AUDIT_WORKSHEET_NAME = "M1-normalization-audit-worksheet.md"
SOURCE_MANIFEST_RELATIVE = ("data", "manifests", "candidate_source_manifest.json")
AUDIT_REQUEST_RELATIVE = ("reviews", "M1-normalization-audit-request.yaml")

Outcome = Literal["success", "failure"]
SizeBand = Literal["short", "middle", "long"]


class NormalizationAuditPreparationError(M1PreparationError):
    """Raised when a private normalization-audit sample cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class AuditCandidate:
    example_id: str
    task_family_id: str
    task_id: str
    trial: int
    packet_size_bytes: int
    size_band: SizeBand
    source_outcome: Outcome
    source_reward: float
    source_simulation_id: str
    source_simulation_index: int
    source_simulation: dict[str, Any]
    packet: dict[str, Any]
    packet_path: str
    packet_hash: str
    source_simulation_hash: str


@dataclass(frozen=True, slots=True)
class NormalizationAuditPreparation:
    inventory: dict[str, Any]
    review_aid: dict[str, Any]
    inventory_path: Path
    review_aid_path: Path
    worksheet_path: Path


def prepare_m1_normalization_audit(root: Path) -> NormalizationAuditPreparation:
    """Prepare, but never approve, the private ten-case M1 normalization audit.

    The complete M1 proposal is revalidated offline first. All new artifacts stay
    below the already ignored packet root, and existing non-equivalent artifacts
    are never overwritten.
    """

    resolved_root = root.resolve()
    m1 = prepare_m1(resolved_root)
    audit_root = safe_join(
        resolved_root,
        *PACKET_ROOT_RELATIVE,
        AUDIT_ROOT_NAME,
    )
    if not audit_root.is_relative_to(m1.packet_root.resolve()):
        raise NormalizationAuditPreparationError(
            "normalization-audit artifacts must remain under the private packet root"
        )
    return _prepare_m1_normalization_audit_internal(
        resolved_root,
        m1=m1,
        audit_root=audit_root,
    )


def _prepare_m1_normalization_audit_internal(
    root: Path,
    *,
    m1: M1Preparation,
    audit_root: Path,
) -> NormalizationAuditPreparation:
    """Internal writer with an injectable private output root for tests."""

    resolved_root = root.resolve()
    resolved_audit_root = audit_root.resolve()
    private_manifest_path = m1.packet_root / "corpus_manifest.json"
    private_manifest = _load_json_object(
        private_manifest_path,
        context="private packet corpus manifest",
    )
    _validate_sealed_manifest(
        private_manifest,
        context="private packet corpus manifest",
    )
    if private_manifest.get("manifest_hash") != m1.corpus_manifest.get(
        "bindings", {}
    ).get("private_packet_corpus_manifest_hash"):
        raise NormalizationAuditPreparationError(
            "private packet corpus does not match the M1 corpus proposal"
        )

    source_manifest_path = safe_join(resolved_root, *SOURCE_MANIFEST_RELATIVE)
    expected_source_manifest_hash = m1.corpus_manifest.get("bindings", {}).get(
        "candidate_source_manifest_sha256"
    )
    if sha256_file(source_manifest_path) != expected_source_manifest_hash:
        raise NormalizationAuditPreparationError(
            "candidate source manifest does not match the M1 proposal"
        )
    source_manifest = _load_json_object(
        source_manifest_path,
        context="candidate source manifest",
    )
    trajectory_path, trajectory_sha256 = _validated_trajectory_path(
        resolved_root,
        source_manifest,
        expected_source_sha256=str(m1.corpus_manifest.get("source_sha256")),
    )
    source = _load_json_object(trajectory_path, context="source trajectory corpus")
    simulations = source.get("simulations")
    if not isinstance(simulations, list):
        raise NormalizationAuditPreparationError(
            "source trajectory corpus has no simulations array"
        )
    simulations_by_id: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, simulation in enumerate(simulations):
        if not isinstance(simulation, dict):
            raise NormalizationAuditPreparationError(
                f"source simulation {index} is not an object"
            )
        simulation_id = simulation.get("id")
        if not isinstance(simulation_id, str) or not simulation_id.strip():
            raise NormalizationAuditPreparationError(
                f"source simulation {index} has no nonblank ID"
            )
        if simulation_id in simulations_by_id:
            raise NormalizationAuditPreparationError(
                f"duplicate source simulation ID: {simulation_id}"
            )
        simulations_by_id[simulation_id] = (index, simulation)

    public_entries = _unique_entries_by_id(
        m1.corpus_manifest.get("entries"),
        context="M1 corpus proposal",
    )
    private_entries = _unique_entries_by_id(
        private_manifest.get("entries"),
        context="private packet corpus",
    )
    if set(public_entries) != set(private_entries):
        raise NormalizationAuditPreparationError(
            "public and private packet corpus example IDs differ"
        )

    pool_entries = [
        *m1.pool_manifest.get("model_judgment", []),
        *m1.pool_manifest.get("annotation_calibration", []),
    ]
    if not all(
        isinstance(entry, Mapping)
        and isinstance(entry.get("task_family_id"), str)
        for entry in pool_entries
    ):
        raise NormalizationAuditPreparationError("M1 measured-pool entries are malformed")
    measured_families = {str(entry["task_family_id"]) for entry in pool_entries}

    eligible_entries = [
        entry
        for entry in public_entries.values()
        if entry.get("split") == "test"
        and entry.get("task_family_id") not in measured_families
        and entry.get("task_family_id") != TIMING_EXPOSED_FAMILY
    ]
    if not eligible_entries:
        raise NormalizationAuditPreparationError(
            "no otherwise-unselected test packets are available for the audit"
        )
    short_threshold, long_threshold = _size_thresholds(
        [entry.get("packet_size_bytes") for entry in eligible_entries]
    )
    candidates = [
        _build_candidate(
            entry,
            private_entry=private_entries[str(entry["example_id"])],
            packet_root=m1.packet_root,
            simulations_by_id=simulations_by_id,
            short_threshold=short_threshold,
            long_threshold=long_threshold,
        )
        for entry in eligible_entries
    ]
    selected = _select_audit_candidates(
        candidates,
        seed=int(m1.pool_manifest.get("selection_seed")),
    )
    _validate_selected_sample(
        selected,
        measured_families=measured_families,
    )

    inventory = {
        "schema_version": M1_SCHEMA_VERSION,
        "inventory_type": "m1_normalization_audit_reviewed_examples",
        "private_packet_corpus_manifest_hash": private_manifest["manifest_hash"],
        "proposal_corpus_manifest_hash": m1.corpus_manifest["manifest_hash"],
        "example_ids": [candidate.example_id for candidate in selected],
    }
    inventory_text = _json_text(inventory)
    inventory_sha256 = sha256_bytes(inventory_text.encode("utf-8"))

    review_aid = _build_review_aid(
        selected,
        m1=m1,
        private_manifest=private_manifest,
        trajectory_path=trajectory_path,
        trajectory_sha256=trajectory_sha256,
        source_manifest_path=source_manifest_path,
        inventory_sha256=inventory_sha256,
        short_threshold=short_threshold,
        long_threshold=long_threshold,
        root=resolved_root,
    )
    review_aid_text = _json_text(review_aid)
    review_aid_sha256 = sha256_bytes(review_aid_text.encode("utf-8"))
    worksheet_text = _build_worksheet(
        selected,
        inventory_sha256=inventory_sha256,
        review_aid_sha256=review_aid_sha256,
        short_threshold=short_threshold,
        long_threshold=long_threshold,
    )

    inventory_path = resolved_audit_root / Path(*HUMAN_AUDIT_INVENTORY_RELATIVE).name
    review_aid_path = resolved_audit_root / AUDIT_REVIEW_AID_NAME
    worksheet_path = resolved_audit_root / AUDIT_WORKSHEET_NAME
    expected_outputs = {
        inventory_path: inventory_text,
        review_aid_path: review_aid_text,
        worksheet_path: worksheet_text,
    }
    _preflight_private_outputs(expected_outputs)
    for path, text in expected_outputs.items():
        if not path.exists():
            write_text_atomic(path, text)

    return NormalizationAuditPreparation(
        inventory=inventory,
        review_aid=review_aid,
        inventory_path=inventory_path,
        review_aid_path=review_aid_path,
        worksheet_path=worksheet_path,
    )


def _validated_trajectory_path(
    root: Path,
    source_manifest: Mapping[str, Any],
    *,
    expected_source_sha256: str,
) -> tuple[Path, str]:
    artifacts = source_manifest.get("artifacts")
    trajectory = artifacts.get("trajectory") if isinstance(artifacts, Mapping) else None
    if not isinstance(trajectory, Mapping):
        raise NormalizationAuditPreparationError(
            "candidate source manifest has no trajectory artifact"
        )
    relative = trajectory.get("path")
    expected_hash = trajectory.get("sha256")
    expected_size = trajectory.get("size_bytes")
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise NormalizationAuditPreparationError("source trajectory path is not safe and relative")
    if not isinstance(expected_hash, str) or expected_hash != expected_source_sha256:
        raise NormalizationAuditPreparationError(
            "source trajectory hash is not bound to the M1 proposal"
        )
    path = safe_join(root, *Path(relative).parts)
    if not path.is_file() or path.stat().st_size != expected_size:
        raise NormalizationAuditPreparationError("source trajectory size does not match its manifest")
    if sha256_file(path) != expected_hash:
        raise NormalizationAuditPreparationError("source trajectory hash does not match its manifest")
    return path, expected_hash


def _build_candidate(
    public_entry: Mapping[str, Any],
    *,
    private_entry: Mapping[str, Any],
    packet_root: Path,
    simulations_by_id: Mapping[str, tuple[int, dict[str, Any]]],
    short_threshold: int,
    long_threshold: int,
) -> AuditCandidate:
    example_id = public_entry.get("example_id")
    family_id = public_entry.get("task_family_id")
    task_id = public_entry.get("task_id")
    trial = public_entry.get("trial")
    packet_size = public_entry.get("packet_size_bytes")
    if (
        not isinstance(example_id, str)
        or not isinstance(family_id, str)
        or not isinstance(task_id, str)
        or isinstance(trial, bool)
        or not isinstance(trial, int)
        or isinstance(packet_size, bool)
        or not isinstance(packet_size, int)
    ):
        raise NormalizationAuditPreparationError(
            "audit-eligible public corpus entry is malformed"
        )
    comparable = ("example_id", "task_id", "trial", "packet_hash", "packet_size_bytes")
    if any(private_entry.get(key) != public_entry.get(key) for key in comparable):
        raise NormalizationAuditPreparationError(
            f"public/private packet entry mismatch: {example_id}"
        )

    source_simulation_id = private_entry.get("source_simulation_id")
    if not isinstance(source_simulation_id, str):
        raise NormalizationAuditPreparationError(
            f"private source simulation ID is missing: {example_id}"
        )
    source_record = simulations_by_id.get(source_simulation_id)
    if source_record is None:
        raise NormalizationAuditPreparationError(
            f"private source simulation is absent: {example_id}"
        )
    source_index, source_simulation = source_record
    if canonical_sha256(source_simulation) != private_entry.get("source_simulation_hash"):
        raise NormalizationAuditPreparationError(
            f"source simulation hash mismatch: {example_id}"
        )
    if (
        str(source_simulation.get("task_id")) != task_id
        or source_simulation.get("trial") != trial
    ):
        raise NormalizationAuditPreparationError(
            f"source task/trial binding mismatch: {example_id}"
        )

    reward_info = source_simulation.get("reward_info")
    reward = reward_info.get("reward") if isinstance(reward_info, Mapping) else None
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        raise NormalizationAuditPreparationError(
            f"source benchmark reward is missing or nonnumeric: {example_id}"
        )
    source_reward = float(reward)
    if source_reward == 1.0:
        source_outcome: Outcome = "success"
    elif source_reward == 0.0:
        source_outcome = "failure"
    else:
        raise NormalizationAuditPreparationError(
            f"source benchmark reward is neither binary success nor failure: {example_id}"
        )

    packet_relative = private_entry.get("packet_path")
    if not isinstance(packet_relative, str) or Path(packet_relative).is_absolute():
        raise NormalizationAuditPreparationError(
            f"private packet path is invalid: {example_id}"
        )
    packet_path = safe_join(packet_root, *Path(packet_relative).parts)
    packet = _load_json_object(packet_path, context=f"canonical packet {example_id}")
    EvidencePacket.model_validate(packet)
    if packet.get("example_id") != example_id:
        raise NormalizationAuditPreparationError(
            f"canonical packet example ID mismatch: {example_id}"
        )
    if canonical_sha256(packet) != private_entry.get("packet_hash"):
        raise NormalizationAuditPreparationError(
            f"canonical packet hash mismatch: {example_id}"
        )
    if len(canonical_json_bytes(packet)) != packet_size:
        raise NormalizationAuditPreparationError(
            f"canonical packet byte size mismatch: {example_id}"
        )

    size_band: SizeBand
    if packet_size <= short_threshold:
        size_band = "short"
    elif packet_size >= long_threshold:
        size_band = "long"
    else:
        size_band = "middle"
    return AuditCandidate(
        example_id=example_id,
        task_family_id=family_id,
        task_id=task_id,
        trial=trial,
        packet_size_bytes=packet_size,
        size_band=size_band,
        source_outcome=source_outcome,
        source_reward=source_reward,
        source_simulation_id=source_simulation_id,
        source_simulation_index=source_index,
        source_simulation=source_simulation,
        packet=packet,
        packet_path=packet_relative,
        packet_hash=str(private_entry["packet_hash"]),
        source_simulation_hash=str(private_entry["source_simulation_hash"]),
    )


def _select_audit_candidates(
    candidates: Sequence[AuditCandidate],
    *,
    seed: int,
) -> tuple[AuditCandidate, ...]:
    """Select ten distinct families with crossed size/outcome coverage."""

    requirements: tuple[tuple[SizeBand | None, Outcome, str], ...] = (
        ("short", "success", "short_success"),
        ("short", "failure", "short_failure"),
        ("long", "success", "long_success"),
        ("long", "failure", "long_failure"),
        (None, "success", "additional_success_1"),
        (None, "success", "additional_success_2"),
        (None, "success", "additional_success_3"),
        (None, "failure", "additional_failure_1"),
        (None, "failure", "additional_failure_2"),
        (None, "failure", "additional_failure_3"),
    )
    if len(candidates) != len({candidate.example_id for candidate in candidates}):
        raise NormalizationAuditPreparationError("audit candidates contain duplicate example IDs")

    options: list[list[AuditCandidate]] = []
    for size_band, outcome, namespace in requirements:
        matching = [
            candidate
            for candidate in candidates
            if candidate.source_outcome == outcome
            and (size_band is None or candidate.size_band == size_band)
        ]
        if size_band == "short":
            matching.sort(
                key=lambda candidate: (
                    candidate.packet_size_bytes,
                    _selection_rank(seed, namespace, candidate.example_id),
                )
            )
        elif size_band == "long":
            matching.sort(
                key=lambda candidate: (
                    -candidate.packet_size_bytes,
                    _selection_rank(seed, namespace, candidate.example_id),
                )
            )
        else:
            matching.sort(
                key=lambda candidate: _selection_rank(
                    seed,
                    namespace,
                    candidate.example_id,
                )
            )
        if not matching:
            raise NormalizationAuditPreparationError(
                f"audit frame cannot satisfy required stratum: {namespace}"
            )
        options.append(matching)

    selected: list[AuditCandidate] = []
    used_families: set[str] = set()

    def assign(index: int) -> bool:
        if index == len(requirements):
            return True
        for candidate in options[index]:
            if candidate.task_family_id in used_families:
                continue
            selected.append(candidate)
            used_families.add(candidate.task_family_id)
            if assign(index + 1):
                return True
            used_families.remove(candidate.task_family_id)
            selected.pop()
        return False

    if not assign(0):
        raise NormalizationAuditPreparationError(
            "audit frame cannot supply ten distinct families with required coverage"
        )
    return tuple(selected)


def _validate_selected_sample(
    selected: Sequence[AuditCandidate],
    *,
    measured_families: set[str],
) -> None:
    ids = {candidate.example_id for candidate in selected}
    families = {candidate.task_family_id for candidate in selected}
    outcomes = [candidate.source_outcome for candidate in selected]
    cells = {(candidate.size_band, candidate.source_outcome) for candidate in selected}
    if len(selected) != AUDIT_CASE_COUNT or len(ids) != AUDIT_CASE_COUNT:
        raise NormalizationAuditPreparationError("audit sample must contain exactly ten packets")
    if len(families) != AUDIT_CASE_COUNT:
        raise NormalizationAuditPreparationError(
            "audit sample must contain ten distinct task families"
        )
    if families & measured_families or TIMING_EXPOSED_FAMILY in families:
        raise NormalizationAuditPreparationError(
            "audit sample overlaps a measured or timing-exposed family"
        )
    if outcomes.count("success") != AUDIT_OUTCOME_COUNT or outcomes.count(
        "failure"
    ) != AUDIT_OUTCOME_COUNT:
        raise NormalizationAuditPreparationError(
            "audit sample must contain five source successes and five source failures"
        )
    required_cells = {
        ("short", "success"),
        ("short", "failure"),
        ("long", "success"),
        ("long", "failure"),
    }
    if not required_cells.issubset(cells):
        raise NormalizationAuditPreparationError(
            "audit sample lacks crossed short/long and success/failure coverage"
        )


def _build_review_aid(
    selected: Sequence[AuditCandidate],
    *,
    m1: M1Preparation,
    private_manifest: Mapping[str, Any],
    trajectory_path: Path,
    trajectory_sha256: str,
    source_manifest_path: Path,
    inventory_sha256: str,
    short_threshold: int,
    long_threshold: int,
    root: Path,
) -> dict[str, Any]:
    request_path = safe_join(root, *AUDIT_REQUEST_RELATIVE)
    return {
        "schema_version": M1_SCHEMA_VERSION,
        "artifact_type": "private_m1_normalization_audit_review_aid",
        "status": "prepared_for_human_review_not_reviewed_or_approved",
        "privacy": "private_gitignored_contains_source_content_rewards_and_ids",
        "model_calls_made": 0,
        "network_access_used": False,
        "human_signoff_present": False,
        "selection": {
            "case_count": AUDIT_CASE_COUNT,
            "distinct_task_families": AUDIT_CASE_COUNT,
            "split": "test",
            "provisional_pool_family_overlap": 0,
            "timing_exposed_family_overlap": 0,
            "selection_seed": m1.pool_manifest["selection_seed"],
            "selection_method": (
                "deterministic_hash_rank_with_distinct_family_constraints_and_"
                "crossed_size_outcome_coverage"
            ),
            "source_outcome_definition": (
                "success=source reward_info.reward exactly 1.0; "
                "failure=source reward_info.reward exactly 0.0"
            ),
            "packet_size_metric": "canonical_json_utf8_bytes",
            "short_maximum_bytes": short_threshold,
            "long_minimum_bytes": long_threshold,
            "threshold_population": "otherwise_unselected_test_packets",
            "threshold_method": "higher_order_statistic_q25_q75",
            "source_success_cases": AUDIT_OUTCOME_COUNT,
            "source_failure_cases": AUDIT_OUTCOME_COUNT,
        },
        "bindings": {
            "audit_request_sha256": sha256_file(request_path),
            "inventory_sha256": inventory_sha256,
            "private_packet_corpus_manifest_hash": private_manifest["manifest_hash"],
            "proposal_corpus_manifest_hash": m1.corpus_manifest["manifest_hash"],
            "proposal_split_manifest_hash": m1.split_manifest["manifest_hash"],
            "proposal_pool_manifest_hash": m1.pool_manifest["manifest_hash"],
            "proposal_exclusion_manifest_hash": m1.exclusion_manifest["manifest_hash"],
            "candidate_source_manifest_path": _relative_or_absolute(
                root,
                source_manifest_path,
            ),
            "candidate_source_manifest_sha256": sha256_file(source_manifest_path),
            "source_trajectory_path": _relative_or_absolute(root, trajectory_path),
            "source_trajectory_sha256": trajectory_sha256,
        },
        "required_checks": list(HUMAN_AUDIT_REQUIRED_CHECKS),
        "cases": [
            {
                "case_number": case_number,
                "example_id": candidate.example_id,
                "task_family_id": candidate.task_family_id,
                "task_id": candidate.task_id,
                "trial": candidate.trial,
                "packet_size_bytes": candidate.packet_size_bytes,
                "size_band": candidate.size_band,
                "source_outcome": candidate.source_outcome,
                "source_reward": candidate.source_reward,
                "source_simulation_id": candidate.source_simulation_id,
                "source_simulation_index": candidate.source_simulation_index,
                "source_simulation_hash": candidate.source_simulation_hash,
                "canonical_packet_path": (
                    Path(*PACKET_ROOT_RELATIVE, candidate.packet_path).as_posix()
                ),
                "canonical_packet_hash": candidate.packet_hash,
                "original_source_simulation": candidate.source_simulation,
                "canonical_packet": candidate.packet,
                "review": {
                    "checks": {
                        check: None for check in HUMAN_AUDIT_REQUIRED_CHECKS
                    },
                    "notes": None,
                },
            }
            for case_number, candidate in enumerate(selected, start=1)
        ],
    }


def _build_worksheet(
    selected: Sequence[AuditCandidate],
    *,
    inventory_sha256: str,
    review_aid_sha256: str,
    short_threshold: int,
    long_threshold: int,
) -> str:
    lines = [
        "# Private M1 source-normalization audit worksheet",
        "",
        "**Status: PREPARED ONLY — NOT REVIEWED OR APPROVED.**",
        "",
        "This gitignored worksheet and its review aid contain private source IDs, "
        "benchmark rewards, and conversation content. Do not copy them into public manifests.",
        "",
        f"- Inventory SHA-256: `{inventory_sha256}`",
        f"- Review-aid SHA-256: `{review_aid_sha256}`",
        f"- Review aid: `{AUDIT_REVIEW_AID_NAME}`",
        f"- Selection: {AUDIT_CASE_COUNT} packets from {AUDIT_CASE_COUNT} distinct, "
        "otherwise-unselected test families",
        f"- Outcome balance: {AUDIT_OUTCOME_COUNT} source successes and "
        f"{AUDIT_OUTCOME_COUNT} source failures",
        f"- Short: at most {short_threshold} canonical JSON bytes; long: at least "
        f"{long_threshold} bytes",
        "",
        "For each case, compare `original_source_simulation` with `canonical_packet` in "
        "the review aid. Check a box only after inspecting that case. Leave the public "
        "decision pending until every case and the overall sign-off are complete.",
        "",
    ]
    labels = {
        "conversation_order_matches_source": "Conversation order matches the source",
        "target_response_matches_source": "Target response matches the source",
        "terminal_future_message_excluded": "Terminal future message is excluded",
        "tool_calls_and_results_preserved_and_paired": (
            "Tool calls and results are preserved and paired"
        ),
        "policy_and_tool_definitions_match_pinned_source": (
            "Policy and tool definitions match the pinned source"
        ),
        "forbidden_fields_absent": "Forbidden fields are absent from the canonical packet",
    }
    for case_number, candidate in enumerate(selected, start=1):
        lines.extend(
            [
                f"## Case {case_number:02d}",
                "",
                f"- Example ID: `{candidate.example_id}`",
                f"- Task family: `{candidate.task_family_id}`",
                f"- Source outcome: `{candidate.source_outcome}` "
                f"(benchmark reward `{candidate.source_reward:.1f}`)",
                f"- Size: `{candidate.packet_size_bytes}` bytes (`{candidate.size_band}`)",
                f"- Review-aid case index: `{case_number - 1}`",
                "",
            ]
        )
        lines.extend(
            f"- [ ] {labels[check]}" for check in HUMAN_AUDIT_REQUIRED_CHECKS
        )
        lines.extend(["", "Notes:", "", "", ""])
    lines.extend(
        [
            "## Overall human sign-off",
            "",
            "- Reviewer: ____________________",
            "- Reviewed at (timezone-aware): ____________________",
            "- [ ] All ten cases were reviewed against the bound source and packet",
            "- [ ] All six checks passed for every case",
            "- [ ] Short and long packets plus source successes and failures were observed",
            "- [ ] No measured-pool or timing-exposed family was reviewed",
            "",
            "Conditions / notes:",
            "",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _size_thresholds(values: Sequence[Any]) -> tuple[int, int]:
    if not values or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in values
    ):
        raise NormalizationAuditPreparationError(
            "audit packet sizes must be nonempty positive integers"
        )
    ordered = sorted(values)
    short = ordered[math.ceil((len(ordered) - 1) * 0.25)]
    long = ordered[math.ceil((len(ordered) - 1) * 0.75)]
    if short >= long:
        raise NormalizationAuditPreparationError(
            "audit packet-size frame cannot distinguish short and long cases"
        )
    return short, long


def _selection_rank(seed: int, namespace: str, example_id: str) -> str:
    return canonical_sha256(
        {
            "seed": seed,
            "namespace": f"m1-normalization-audit:{namespace}",
            "example_id": example_id,
        }
    )


def _unique_entries_by_id(value: Any, *, context: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(entry, Mapping) for entry in value):
        raise NormalizationAuditPreparationError(f"{context} entries are malformed")
    output: dict[str, Mapping[str, Any]] = {}
    for entry in value:
        example_id = entry.get("example_id")
        if not isinstance(example_id, str) or not example_id.strip():
            raise NormalizationAuditPreparationError(
                f"{context} contains an entry without an example ID"
            )
        if example_id in output:
            raise NormalizationAuditPreparationError(
                f"{context} contains a duplicate example ID: {example_id}"
            )
        output[example_id] = entry
    return output


def _validate_sealed_manifest(manifest: Mapping[str, Any], *, context: str) -> None:
    recorded = manifest.get("manifest_hash")
    if not isinstance(recorded, str):
        raise NormalizationAuditPreparationError(f"{context} has no manifest hash")
    content = dict(manifest)
    content.pop("manifest_hash")
    if canonical_sha256(content) != recorded:
        raise NormalizationAuditPreparationError(f"{context} manifest hash is invalid")


def _preflight_private_outputs(expected: Mapping[Path, str]) -> None:
    for path, text in expected.items():
        if path.exists():
            if not path.is_file():
                raise NormalizationAuditPreparationError(
                    f"private audit output is not a regular file: {path}"
                )
            if path.read_bytes() != text.encode("utf-8"):
                raise NormalizationAuditPreparationError(
                    f"refusing to overwrite drifted private audit artifact: {path}"
                )


def _load_json_object(path: Path, *, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NormalizationAuditPreparationError(
            f"could not read {context} at {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise NormalizationAuditPreparationError(f"{context} must be a JSON object")
    return value


def _json_text(value: Any) -> str:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise NormalizationAuditPreparationError(
            f"private audit artifact is not canonical JSON data: {exc}"
        ) from exc


def _relative_or_absolute(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(root.resolve()):
        return resolved.relative_to(root.resolve()).as_posix()
    return resolved.as_posix()
