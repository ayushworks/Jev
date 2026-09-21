from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from judge_compare.annotation import (
    CRITERIA,
    AnnotationLabel,
    BlindedAnnotationAssignment,
    serialize_blinded_assignment,
)
from judge_compare.hashing import canonical_sha256, sha256_bytes
from judge_compare.m1 import (
    HUMAN_AUDIT_DECISION_RELATIVE,
    HUMAN_AUDIT_INVENTORY_RELATIVE,
    HUMAN_AUDIT_REQUEST_RELATIVE,
    HUMAN_AUDIT_RESOLUTION_NAME,
    PACKET_ROOT_RELATIVE,
    PUBLIC_MANIFEST_NAMES,
    M1PreparationError,
    _build_normalization_audit_resolution,
    _load_and_validate_m1_proposals,
    _load_json_object,
    _validate_bound_private_corpus,
    _validate_human_normalization_audit_decision,
    _validate_human_normalization_audit_request,
    _validate_reviewed_id_inventory,
)
from judge_compare.models import EvidencePacket
from judge_compare.paths import safe_join

M2_SCHEMA_VERSION = "1.0"
SINGLE_REVIEWER_NAME = "Ayush"
CALIBRATION_CASES = 24
CALIBRATION_MINIMUM_FAMILIES = 12
MODEL_JUDGMENT_CASES_MAXIMUM = 21
RUBRIC_VERSION = "rubric-draft-v1-single-reviewer-development"
ASSIGNMENT_ROOT_RELATIVE = (
    "annotations",
    "rubric-development",
    "v1",
    "preparation",
)
ASSIGNMENT_BUNDLE_NAME = "single-reviewer-assignment-bundle.json"
WORKSHEET_NAME = "single-reviewer-worksheet.md"
SINGLE_REVIEWER_DECISION_RELATIVE = (
    "reviews",
    "single-reviewer-annotation-decision.yaml",
)
SINGLE_REVIEWER_AMENDMENT_RELATIVE = (
    "protocol",
    "single-reviewer-annotation-amendment.md",
)
RUBRIC_RELATIVE = ("protocol", "rubric-draft.md")
MANIFEST_ROOT_RELATIVE = ("data", "manifests")

_DECISION_KEYS = {
    "schema_version",
    "gate",
    "decision",
    "reviewer",
    "reviewed_at",
    "conditions",
    "amendment_path",
    "amendment_sha256",
    "bindings",
    "scope",
}
_DECISION_BINDING_PATHS = {
    "g0_decision_sha256": ("reviews", "G0-decision.yaml"),
    "g0_package_sha256": ("reviews", "G0-package.json"),
    "protocol_v2_sha256": ("protocol", "protocol-v2.md"),
    "active_config_sha256": ("configs", "experiment.yaml"),
    "active_rubric_draft_sha256": RUBRIC_RELATIVE,
    "original_human_annotation_plan_sha256": (
        "protocol",
        "human-annotation-plan.md",
    ),
    "original_annotation_plan_request_sha256": (
        "reviews",
        "annotation-plan-request.md",
    ),
    "annotation_timing_estimate_sha256": (
        "reviews",
        "annotation-timing-estimate.yaml",
    ),
}
_DECISION_BINDING_KEYS = {
    *_DECISION_BINDING_PATHS,
    "m1_pool_manifest_file_sha256",
    "m1_pool_manifest_hash",
}
_DECISION_SCOPE = {
    "reviewer_count": 1,
    "named_reviewer": SINGLE_REVIEWER_NAME,
    "calibration_cases": CALIBRATION_CASES,
    "calibration_minimum_task_families": CALIBRATION_MINIMUM_FAMILIES,
    "calibration_purpose": "rubric_development_only",
    "model_judgment_cases_maximum": MODEL_JUDGMENT_CASES_MAXIMUM,
    "independent_double_annotation": False,
    "adjudication": "not_applicable",
    "inter_rater_metrics": "not_applicable",
    "cash_budget_usd": 0,
    "claim_scope": (
        "agreement_with_ayush_reference_labels_not_objective_accuracy"
    ),
}
_LABEL_OPTIONS: tuple[AnnotationLabel, ...] = (
    "PASS",
    "FAIL",
    "NOT_APPLICABLE",
    "UNSCORABLE",
)


class RubricDevelopmentPreparationError(M1PreparationError):
    """Raised when single-reviewer rubric development cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class SingleReviewerRubricDevelopmentPreparation:
    bundle: dict[str, Any]
    bundle_path: Path
    worksheet_path: Path


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    path: Path
    payload: bytes
    sha256: str


def prepare_single_reviewer_rubric_development(
    root: Path,
) -> SingleReviewerRubricDevelopmentPreparation:
    """Prepare the private 24-case Ayush rubric-development assignment offline.

    This operation requires both the approved post-G0 single-reviewer amendment
    and the append-only M1 normalization-audit resolution. It never prepares or
    exposes model-judgment packets, creates labels, or imports a network/model
    client.
    """

    resolved_root = root.resolve()
    output_root = resolved_root.joinpath(*ASSIGNMENT_ROOT_RELATIVE)
    _assert_canonical_private_output_is_ignored(resolved_root, output_root)
    return _prepare_single_reviewer_rubric_development_internal(
        resolved_root,
        manifest_root=safe_join(resolved_root, *MANIFEST_ROOT_RELATIVE),
        packet_root=safe_join(resolved_root, *PACKET_ROOT_RELATIVE),
        output_root=output_root,
        decision_path=safe_join(
            resolved_root,
            *SINGLE_REVIEWER_DECISION_RELATIVE,
        ),
        amendment_path=safe_join(
            resolved_root,
            *SINGLE_REVIEWER_AMENDMENT_RELATIVE,
        ),
        rubric_path=safe_join(resolved_root, *RUBRIC_RELATIVE),
        normalization_audit_decision_path=safe_join(
            resolved_root,
            *HUMAN_AUDIT_DECISION_RELATIVE,
        ),
        normalization_audit_resolution_path=safe_join(
            resolved_root,
            *MANIFEST_ROOT_RELATIVE,
            HUMAN_AUDIT_RESOLUTION_NAME,
        ),
    )


def _prepare_single_reviewer_rubric_development_internal(
    root: Path,
    *,
    manifest_root: Path,
    packet_root: Path,
    output_root: Path,
    decision_path: Path,
    amendment_path: Path,
    rubric_path: Path,
    normalization_audit_decision_path: Path,
    normalization_audit_resolution_path: Path,
) -> SingleReviewerRubricDevelopmentPreparation:
    """Internal writer whose artifact paths are injectable for isolated tests."""

    resolved_root = root.resolve()
    resolved_manifest_root = manifest_root.resolve()
    resolved_packet_root = packet_root.resolve()
    # Preserve the lexical path so the no-follow writer can still identify a
    # symlink passed through the internal test seam.
    resolved_output_root = output_root.absolute()
    resolved_decision_path = decision_path.resolve()
    resolved_amendment_path = amendment_path.resolve()
    resolved_rubric_path = rubric_path.resolve()
    resolved_audit_decision_path = normalization_audit_decision_path.resolve()
    resolved_audit_resolution_path = normalization_audit_resolution_path.resolve()

    request_path = safe_join(resolved_root, *HUMAN_AUDIT_REQUEST_RELATIVE)
    if not resolved_audit_resolution_path.is_file():
        raise RubricDevelopmentPreparationError(
            "single-reviewer assignments require the completed append-only M1 "
            "normalization-audit resolution"
        )
    inventory_path = resolved_packet_root / Path(*HUMAN_AUDIT_INVENTORY_RELATIVE)
    fixed_snapshots = _capture_file_snapshots(
        _fixed_approved_input_paths(
            resolved_root,
            manifest_root=resolved_manifest_root,
            private_manifest_path=resolved_packet_root / "corpus_manifest.json",
            decision_path=resolved_decision_path,
            amendment_path=resolved_amendment_path,
            rubric_path=resolved_rubric_path,
            normalization_audit_decision_path=resolved_audit_decision_path,
            normalization_audit_resolution_path=resolved_audit_resolution_path,
            inventory_path=inventory_path,
        )
    )
    audit_packet_paths = _audit_packet_paths_from_snapshots(
        packet_root=resolved_packet_root,
        private_manifest=_json_from_snapshot(
            fixed_snapshots[(resolved_packet_root / "corpus_manifest.json").resolve()],
            context="private packet corpus manifest snapshot",
        ),
        inventory=_json_from_snapshot(
            fixed_snapshots[inventory_path.resolve()],
            context="normalization-audit inventory snapshot",
        ),
    )
    fixed_snapshots.update(_capture_file_snapshots(audit_packet_paths))

    request_summary = _validate_human_normalization_audit_request(request_path)
    if request_summary.get("request_sha256") != fixed_snapshots[
        request_path.resolve()
    ].sha256:
        raise RubricDevelopmentPreparationError(
            "normalization-audit request changed during validation"
        )
    proposals = _load_and_validate_m1_proposals(
        resolved_root,
        manifest_root=resolved_manifest_root,
        request_summary=request_summary,
    )
    for key, filename in PUBLIC_MANIFEST_NAMES.items():
        path = (resolved_manifest_root / filename).resolve()
        if proposals[key] != _json_from_snapshot(
            fixed_snapshots[path],
            context=f"M1 {key} proposal snapshot",
        ):
            raise RubricDevelopmentPreparationError(
                f"M1 {key} proposal changed during validation"
            )
    private_manifest_path = resolved_packet_root / "corpus_manifest.json"
    private_manifest = _validate_bound_private_corpus(
        resolved_root,
        packet_root=resolved_packet_root,
        private_manifest_path=private_manifest_path,
        corpus_proposal=proposals["corpus"],
    )
    if private_manifest != _json_from_snapshot(
        fixed_snapshots[private_manifest_path.resolve()],
        context="private packet corpus manifest snapshot",
    ):
        raise RubricDevelopmentPreparationError(
            "private packet corpus manifest changed during validation"
        )
    audit_resolution = _validate_completed_normalization_audit(
        resolved_root,
        request_path=request_path,
        request_summary=request_summary,
        proposals=proposals,
        manifest_root=resolved_manifest_root,
        packet_root=resolved_packet_root,
        private_manifest_path=private_manifest_path,
        private_manifest=private_manifest,
        decision_path=resolved_audit_decision_path,
        resolution_path=resolved_audit_resolution_path,
    )
    if audit_resolution != _json_from_snapshot(
        fixed_snapshots[resolved_audit_resolution_path],
        context="normalization-audit resolution snapshot",
    ):
        raise RubricDevelopmentPreparationError(
            "normalization-audit resolution changed during validation"
        )
    decision = _validate_single_reviewer_decision(
        resolved_root,
        decision_path=resolved_decision_path,
        amendment_path=resolved_amendment_path,
        rubric_path=resolved_rubric_path,
        manifest_root=resolved_manifest_root,
        pool_manifest=proposals["pool"],
        snapshots=fixed_snapshots,
    )

    calibration, model_ids, model_families = _validate_calibration_pool(
        proposals["pool"]
    )
    public_entries = _entries_by_id(
        proposals["corpus"].get("entries"),
        context="M1 public corpus",
    )
    private_entries = _entries_by_id(
        private_manifest.get("entries"),
        context="M1 private packet corpus",
    )
    ordered = _deterministic_reviewer_order(
        calibration,
        reviewer=SINGLE_REVIEWER_NAME,
        seed=int(proposals["pool"]["selection_seed"]),
        decision_sha256=fixed_snapshots[resolved_decision_path].sha256,
    )
    assignment_packet_paths = _assignment_packet_paths(
        ordered,
        packet_root=resolved_packet_root,
        private_entries=private_entries,
    )
    fixed_snapshots.update(_capture_file_snapshots(assignment_packet_paths.values()))
    assignments = _build_assignments(
        ordered,
        packet_root=resolved_packet_root,
        public_entries=public_entries,
        private_entries=private_entries,
        rubric_hash=fixed_snapshots[resolved_rubric_path].sha256,
        forbidden_model_ids=model_ids,
        forbidden_model_families=model_families,
        packet_snapshots={
            example_id: fixed_snapshots[path.resolve()]
            for example_id, path in assignment_packet_paths.items()
        },
    )

    bundle = _build_bundle(
        assignments,
        root=resolved_root,
        manifest_root=resolved_manifest_root,
        private_manifest_path=private_manifest_path,
        private_manifest=private_manifest,
        proposals=proposals,
        decision_path=resolved_decision_path,
        decision=decision,
        amendment_path=resolved_amendment_path,
        rubric_path=resolved_rubric_path,
        normalization_audit_resolution_path=resolved_audit_resolution_path,
        normalization_audit_resolution=audit_resolution,
        snapshots=fixed_snapshots,
    )
    bundle_text = _json_text(bundle)
    bundle_sha256 = sha256_bytes(bundle_text.encode("utf-8"))
    worksheet_text = _build_worksheet(
        assignments,
        bundle_sha256=bundle_sha256,
        rubric_sha256=fixed_snapshots[resolved_rubric_path].sha256,
        amendment_sha256=fixed_snapshots[resolved_amendment_path].sha256,
        rubric_text=_text_from_snapshot(
            fixed_snapshots[resolved_rubric_path],
            context="rubric snapshot",
        ),
    )

    bundle_path = resolved_output_root / ASSIGNMENT_BUNDLE_NAME
    worksheet_path = resolved_output_root / WORKSHEET_NAME
    expected = {
        ASSIGNMENT_BUNDLE_NAME: bundle_text.encode("utf-8"),
        WORKSHEET_NAME: worksheet_text.encode("utf-8"),
    }
    _write_private_outputs(
        resolved_output_root,
        expected,
        input_snapshots=tuple(fixed_snapshots.values()),
    )

    return SingleReviewerRubricDevelopmentPreparation(
        bundle=bundle,
        bundle_path=bundle_path,
        worksheet_path=worksheet_path,
    )


def _validate_completed_normalization_audit(
    root: Path,
    *,
    request_path: Path,
    request_summary: Mapping[str, Any],
    proposals: Mapping[str, Mapping[str, Any]],
    manifest_root: Path,
    packet_root: Path,
    private_manifest_path: Path,
    private_manifest: Mapping[str, Any],
    decision_path: Path,
    resolution_path: Path,
) -> dict[str, Any]:
    if not resolution_path.is_file():
        raise RubricDevelopmentPreparationError(
            "single-reviewer assignments require the completed append-only M1 "
            "normalization-audit resolution"
        )
    decision = _validate_human_normalization_audit_decision(
        decision_path,
        request_sha256=str(request_summary["request_sha256"]),
        private_manifest_hash=str(private_manifest["manifest_hash"]),
        proposal_manifest_hashes={
            key: str(proposals[key]["manifest_hash"])
            for key in PUBLIC_MANIFEST_NAMES
        },
    )
    if decision.get("reviewer") != SINGLE_REVIEWER_NAME:
        raise RubricDevelopmentPreparationError(
            "M1 normalization audit must be reviewed by the approved sole human "
            f"reviewer, {SINGLE_REVIEWER_NAME}"
        )
    inventory_path = packet_root / Path(*HUMAN_AUDIT_INVENTORY_RELATIVE)
    inventory_sha256, reviewed_ids = _validate_reviewed_id_inventory(
        inventory_path,
        expected_sha256=str(decision["reviewed_example_ids_inventory_sha256"]),
        expected_private_manifest_hash=str(private_manifest["manifest_hash"]),
        expected_proposal_corpus_hash=str(proposals["corpus"]["manifest_hash"]),
        expected_count=int(decision["packets_reviewed"]),
        packet_root=packet_root,
        private_manifest=private_manifest,
        corpus_proposal=proposals["corpus"],
        pool_proposal=proposals["pool"],
    )
    expected = _build_normalization_audit_resolution(
        root=root,
        request_path=request_path,
        decision_path=decision_path,
        decision=decision,
        inventory_sha256=inventory_sha256,
        private_manifest_path=private_manifest_path,
        private_manifest=private_manifest,
        manifest_root=manifest_root,
        proposals=proposals,
        reviewed_count=len(reviewed_ids),
    )
    observed = _load_json_object(
        resolution_path,
        context="M1 normalization-audit resolution",
    )
    if observed != expected:
        raise RubricDevelopmentPreparationError(
            "M1 normalization-audit resolution is absent, drifted, or not bound "
            "to the active M1 artifacts"
        )
    return observed


def _validate_single_reviewer_decision(
    root: Path,
    *,
    decision_path: Path,
    amendment_path: Path,
    rubric_path: Path,
    manifest_root: Path,
    pool_manifest: Mapping[str, Any],
    snapshots: Mapping[Path, _FileSnapshot],
) -> dict[str, Any]:
    try:
        value = yaml.safe_load(
            _text_from_snapshot(
                snapshots[decision_path.resolve()],
                context="single-reviewer decision snapshot",
            )
        )
    except yaml.YAMLError as error:
        raise RubricDevelopmentPreparationError(
            f"could not load approved single-reviewer decision: {error}"
        ) from error
    if not isinstance(value, dict):
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision must be a YAML object"
        )
    decision: dict[str, Any] = value
    if set(decision) != _DECISION_KEYS:
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision schema is not exact"
        )
    expected_metadata = {
        "schema_version": M2_SCHEMA_VERSION,
        "gate": "annotation-plan-amendment",
        "decision": "approved",
        "reviewer": SINGLE_REVIEWER_NAME,
        "conditions": [],
        "amendment_path": Path(*SINGLE_REVIEWER_AMENDMENT_RELATIVE).as_posix(),
    }
    mismatches = {
        key: {"expected": expected, "observed": decision.get(key)}
        for key, expected in expected_metadata.items()
        if decision.get(key) != expected
    }
    if mismatches:
        raise RubricDevelopmentPreparationError(
            f"single-reviewer decision is not the approved amendment: {mismatches}"
        )
    if not _is_timezone_aware_iso8601(decision.get("reviewed_at")):
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision requires a timezone-aware reviewed_at"
        )
    if decision.get("amendment_sha256") != snapshots[
        amendment_path.resolve()
    ].sha256:
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision does not bind the active amendment"
        )
    scope = decision.get("scope")
    if (
        not isinstance(scope, dict)
        or set(scope) != set(_DECISION_SCOPE)
        or any(scope.get(key) != expected for key, expected in _DECISION_SCOPE.items())
        or isinstance(scope.get("cash_budget_usd"), bool)
    ):
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision scope is not the exact approved zero-cash design"
        )
    bindings = decision.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != _DECISION_BINDING_KEYS:
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision binding schema is not exact"
        )
    for key, relative in _DECISION_BINDING_PATHS.items():
        path = rubric_path if key == "active_rubric_draft_sha256" else safe_join(
            root,
            *relative,
        )
        if bindings.get(key) != snapshots[path.resolve()].sha256:
            raise RubricDevelopmentPreparationError(
                f"single-reviewer decision binding mismatch: {key}"
            )
    pool_path = manifest_root / PUBLIC_MANIFEST_NAMES["pool"]
    if bindings.get("m1_pool_manifest_file_sha256") != snapshots[
        pool_path.resolve()
    ].sha256:
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision does not bind the active M1 pool file"
        )
    if bindings.get("m1_pool_manifest_hash") != pool_manifest.get("manifest_hash"):
        raise RubricDevelopmentPreparationError(
            "single-reviewer decision does not bind the sealed M1 pool manifest"
        )
    return decision


def _validate_calibration_pool(
    pool_manifest: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], frozenset[str], frozenset[str]]:
    calibration = pool_manifest.get("annotation_calibration")
    model_pool = pool_manifest.get("model_judgment")
    counts = pool_manifest.get("counts")
    if not isinstance(calibration, list) or not isinstance(model_pool, list):
        raise RubricDevelopmentPreparationError("M1 pool entries are malformed")
    if len(calibration) != CALIBRATION_CASES:
        raise RubricDevelopmentPreparationError(
            "rubric-development pool must contain exactly 24 cases"
        )
    if len(model_pool) > MODEL_JUDGMENT_CASES_MAXIMUM:
        raise RubricDevelopmentPreparationError(
            "M1 model-judgment pool exceeds the approved maximum"
        )
    calibration_ids: set[str] = set()
    calibration_families: set[str] = set()
    for entry in calibration:
        if not isinstance(entry, Mapping):
            raise RubricDevelopmentPreparationError(
                "M1 rubric-development entry is malformed"
            )
        example_id = entry.get("example_id")
        family = entry.get("task_family_id")
        if (
            not isinstance(example_id, str)
            or not example_id
            or example_id in calibration_ids
            or not isinstance(family, str)
            or not family
            or entry.get("pool") != "annotation_calibration"
            or entry.get("split") != "development"
        ):
            raise RubricDevelopmentPreparationError(
                "M1 rubric-development entries are not distinct development cases"
            )
        calibration_ids.add(example_id)
        calibration_families.add(family)
    if len(calibration_families) < CALIBRATION_MINIMUM_FAMILIES:
        raise RubricDevelopmentPreparationError(
            "rubric-development pool spans fewer than 12 task families"
        )

    model_ids: set[str] = set()
    model_families: set[str] = set()
    for entry in model_pool:
        if not isinstance(entry, Mapping):
            raise RubricDevelopmentPreparationError(
                "M1 model-judgment entry is malformed"
            )
        example_id = entry.get("example_id")
        family = entry.get("task_family_id")
        if not isinstance(example_id, str) or not isinstance(family, str):
            raise RubricDevelopmentPreparationError(
                "M1 model-judgment entry lacks an opaque ID or task family"
            )
        model_ids.add(example_id)
        model_families.add(family)
    if calibration_ids & model_ids or calibration_families & model_families:
        raise RubricDevelopmentPreparationError(
            "rubric-development and model-judgment pools overlap"
        )
    expected_counts = {
        "annotation_calibration_cases": len(calibration),
        "annotation_calibration_families": len(calibration_families),
        "model_judgment_cases": len(model_pool),
        "model_judgment_families": len(model_families),
        "case_overlap": 0,
        "family_overlap": 0,
    }
    if not isinstance(counts, Mapping) or any(
        counts.get(key) != expected for key, expected in expected_counts.items()
    ):
        raise RubricDevelopmentPreparationError("M1 pool counts are inconsistent")
    if pool_manifest.get("outcome_fields_used") != []:
        raise RubricDevelopmentPreparationError(
            "M1 rubric-development selection is not outcome blind"
        )
    return calibration, frozenset(model_ids), frozenset(model_families)


def _deterministic_reviewer_order(
    calibration: Sequence[Mapping[str, Any]],
    *,
    reviewer: str,
    seed: int,
    decision_sha256: str,
) -> list[Mapping[str, Any]]:
    namespace = f"m2-rubric-development|{seed}|{reviewer}|{decision_sha256}"

    def key(entry: Mapping[str, Any]) -> tuple[str, str]:
        example_id = str(entry["example_id"])
        digest = hashlib.sha256(f"{namespace}|{example_id}".encode()).hexdigest()
        return digest, example_id

    return sorted(calibration, key=key)


def _build_assignments(
    ordered: Sequence[Mapping[str, Any]],
    *,
    packet_root: Path,
    public_entries: Mapping[str, Mapping[str, Any]],
    private_entries: Mapping[str, Mapping[str, Any]],
    rubric_hash: str,
    forbidden_model_ids: frozenset[str],
    forbidden_model_families: frozenset[str],
    packet_snapshots: Mapping[str, _FileSnapshot] | None = None,
) -> tuple[BlindedAnnotationAssignment, ...]:
    assignments: list[BlindedAnnotationAssignment] = []
    for order, pool_entry in enumerate(ordered, start=1):
        example_id = str(pool_entry["example_id"])
        if example_id in forbidden_model_ids or (
            pool_entry.get("task_family_id") in forbidden_model_families
        ):
            raise RubricDevelopmentPreparationError(
                "refusing to expose a model-judgment case in rubric development"
            )
        public_entry = public_entries.get(example_id)
        private_entry = private_entries.get(example_id)
        if public_entry is None or private_entry is None:
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet is absent from the bound corpus: {example_id}"
            )
        comparable = (
            "example_id",
            "task_id",
            "trial",
            "packet_hash",
            "packet_size_bytes",
        )
        if any(
            pool_entry.get(key) != public_entry.get(key)
            for key in comparable
            if key in pool_entry
        ) or any(
            private_entry.get(key) != public_entry.get(key) for key in comparable
        ):
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet bindings differ: {example_id}"
            )
        relative = private_entry.get("packet_path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet path is unsafe: {example_id}"
            )
        packet_path = safe_join(packet_root, *Path(relative).parts)
        snapshot = (
            packet_snapshots.get(example_id)
            if packet_snapshots is not None
            else None
        )
        if packet_snapshots is not None and snapshot is None:
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet snapshot is missing: {example_id}"
            )
        if snapshot is not None:
            if snapshot.path != packet_path.resolve():
                raise RubricDevelopmentPreparationError(
                    f"rubric-development packet snapshot path differs: {example_id}"
                )
            packet_value = _json_from_snapshot(
                snapshot,
                context=f"rubric-development packet {example_id} snapshot",
            )
        else:
            packet_value = _load_json_object(
                packet_path,
                context=f"rubric-development packet {example_id}",
            )
        try:
            packet = EvidencePacket.model_validate(packet_value)
        except Exception as error:
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet schema is invalid: {example_id}"
            ) from error
        canonical_packet = packet.model_dump(mode="json")
        if packet_value != canonical_packet:
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet is not canonical: {example_id}"
            )
        packet_hash = canonical_sha256(canonical_packet)
        if (
            packet.example_id != example_id
            or packet_hash != pool_entry.get("packet_hash")
            or len(_canonical_json_bytes(canonical_packet))
            != pool_entry.get("packet_size_bytes")
        ):
            raise RubricDevelopmentPreparationError(
                f"rubric-development packet content drifted: {example_id}"
            )
        assignment_digest = hashlib.sha256(
            (
                f"m2-single-reviewer|{SINGLE_REVIEWER_NAME}|{order}|"
                f"{example_id}|{packet_hash}|{rubric_hash}"
            ).encode()
        ).hexdigest()[:24]
        assignment = BlindedAnnotationAssignment(
            assignment_id=f"rd-{assignment_digest}",
            assignment_order=order,
            pool="annotation_calibration",
            example_id=example_id,
            packet_hash=packet_hash,
            rubric_version=RUBRIC_VERSION,
            rubric_hash=rubric_hash,
            packet=packet,
        )
        # Revalidate through the annotation module's canonical serializer.
        serialize_blinded_assignment(assignment)
        assignments.append(assignment)
    if len(assignments) != CALIBRATION_CASES:
        raise RubricDevelopmentPreparationError(
            "single-reviewer assignment construction did not produce 24 cases"
        )
    return tuple(assignments)


def _build_bundle(
    assignments: Sequence[BlindedAnnotationAssignment],
    *,
    root: Path,
    manifest_root: Path,
    private_manifest_path: Path,
    private_manifest: Mapping[str, Any],
    proposals: Mapping[str, Mapping[str, Any]],
    decision_path: Path,
    decision: Mapping[str, Any],
    amendment_path: Path,
    rubric_path: Path,
    normalization_audit_resolution_path: Path,
    normalization_audit_resolution: Mapping[str, Any],
    snapshots: Mapping[Path, _FileSnapshot],
) -> dict[str, Any]:
    proposal_bindings = {
        key: {
            "path": _display_path(root, manifest_root / filename),
            "file_sha256": snapshots[(manifest_root / filename).resolve()].sha256,
            "manifest_hash": proposals[key]["manifest_hash"],
        }
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    assignment_values = [item.model_dump(mode="json") for item in assignments]
    content: dict[str, Any] = {
        "schema_version": M2_SCHEMA_VERSION,
        "milestone": "M2",
        "artifact_type": "single_reviewer_rubric_development_assignment_bundle",
        "status": "prepared_unlabeled_rubric_development_only",
        "privacy": "private_blinded_human_assignment_packets",
        "reviewer": SINGLE_REVIEWER_NAME,
        "reviewer_count": 1,
        "purpose": "rubric_development_only",
        "claim_scope": _DECISION_SCOPE["claim_scope"],
        "labels_included": False,
        "model_calls_made": 0,
        "network_access_used": False,
        "judge_calls_permitted": False,
        "rubric": {
            "version": RUBRIC_VERSION,
            "criterion_names": list(CRITERIA),
            "label_options": list(_LABEL_OPTIONS),
        },
        "single_reviewer_reference_contract": {
            "record_type": "raw_annotation_submission",
            "submissions_required_per_assignment": 1,
            "final_reference": "validated_immutable_raw_annotation_submission",
            "adjudicated_annotation_record_created": False,
            "inter_rater_metrics_computed": False,
        },
        "pool_boundary": {
            "rubric_development_assignments": len(assignments),
            "model_judgment_cases_maximum": MODEL_JUDGMENT_CASES_MAXIMUM,
            "model_judgment_assignments_prepared": 0,
            "model_judgment_case_identifiers_exposed": 0,
            "model_judgment_packet_contents_exposed": False,
        },
        "randomization": {
            "algorithm": "sha256_namespace_sort_v1",
            "seed": proposals["pool"]["selection_seed"],
            "reviewer": SINGLE_REVIEWER_NAME,
            "decision_sha256_in_namespace": snapshots[
                decision_path.resolve()
            ].sha256,
        },
        "counts": {
            "assignments": len(assignments),
            "criterion_prompts": len(assignments) * len(CRITERIA),
            "labels_produced": 0,
        },
        "bindings": {
            "single_reviewer_decision_path": _display_path(root, decision_path),
            "single_reviewer_decision_sha256": snapshots[
                decision_path.resolve()
            ].sha256,
            "single_reviewer_amendment_path": _display_path(root, amendment_path),
            "single_reviewer_amendment_sha256": snapshots[
                amendment_path.resolve()
            ].sha256,
            "rubric_path": _display_path(root, rubric_path),
            "rubric_sha256": snapshots[rubric_path.resolve()].sha256,
            "normalization_audit_resolution_path": _display_path(
                root,
                normalization_audit_resolution_path,
            ),
            "normalization_audit_resolution_file_sha256": snapshots[
                normalization_audit_resolution_path.resolve()
            ].sha256,
            "normalization_audit_resolution_manifest_hash": (
                normalization_audit_resolution["manifest_hash"]
            ),
            "private_packet_corpus_manifest_file_sha256": snapshots[
                private_manifest_path.resolve()
            ].sha256,
            "private_packet_corpus_manifest_hash": private_manifest["manifest_hash"],
            "m1_proposal_manifests": proposal_bindings,
            "decision_bindings": dict(decision["bindings"]),
        },
        "assignments": assignment_values,
    }
    sealed = dict(content)
    sealed["manifest_hash"] = canonical_sha256(content)
    return sealed


def _build_worksheet(
    assignments: Sequence[BlindedAnnotationAssignment],
    *,
    bundle_sha256: str,
    rubric_sha256: str,
    amendment_sha256: str,
    rubric_text: str,
) -> str:
    first = assignments[0]
    shared_policy = first.packet.policy
    common_tools = [item.model_dump(mode="json") for item in first.packet.tool_definitions]
    for assignment in assignments[1:]:
        if assignment.packet.policy != shared_policy or [
            item.model_dump(mode="json") for item in assignment.packet.tool_definitions
        ] != common_tools:
            raise RubricDevelopmentPreparationError(
                "rubric-development packets do not share the pinned policy/tools"
            )

    lines = [
        "# Single-reviewer rubric-development worksheet",
        "",
        "> **Status:** PREPARED ONLY — UNLABELED. This worksheet contains the",
        "> 24 annotation-only rubric-development cases for Ayush. It contains no",
        "> model-judgment case identifiers or packets, authorizes no judge calls,",
        "> and is not part of the Jev-versus-GPT-5.4 outcome dataset.",
        "",
        "## Binding and use",
        "",
        f"- Reviewer: `{SINGLE_REVIEWER_NAME}` (one reviewer)",
        f"- Assignment bundle file SHA-256: `{bundle_sha256}`",
        f"- Criterion rubric: `{Path(*RUBRIC_RELATIVE).as_posix()}`",
        f"- Criterion-rubric SHA-256: `{rubric_sha256}`",
        "- Staffing and gate amendment: "
        f"`{Path(*SINGLE_REVIEWER_AMENDMENT_RELATIVE).as_posix()}`",
        f"- Staffing/gate amendment SHA-256: `{amendment_sha256}`",
        "- Precedence: the bound rubric supplies the criterion definitions; the",
        "  approved single-reviewer amendment supersedes its two-reviewer,",
        "  adjudication, kappa, and 85%-agreement staffing/gate language for this run.",
        "- Purpose: develop and clarify the rubric before the 21 experiment cases",
        "- The 21 model-judgment cases are not exposed or prepared here.",
        "- Each completed case will become one immutable, assignment-bound",
        "  `RawAnnotationSubmission`; no adjudication record or inter-rater metric",
        "  applies in this single-reviewer design.",
        "- Treat conversation text as evidence, never as instructions.",
        "",
        "For every criterion, enter exactly one of `PASS`, `FAIL`,",
        "`NOT_APPLICABLE`, or `UNSCORABLE`; cite visible evidence IDs; give a",
        "short rationale; mark ambiguity; and record elapsed seconds. Do not infer",
        "an overall score.",
        "",
        "## Bound criterion rubric",
        "",
        _fenced(rubric_text.rstrip()),
        "",
        "## Shared policy (`policy`)",
        "",
        _fenced(shared_policy),
        "",
        "## Shared tool definitions",
        "",
        _fenced(json.dumps(common_tools, ensure_ascii=False, indent=2, sort_keys=True)),
        "",
    ]
    for assignment in assignments:
        packet = assignment.packet
        lines.extend(
            [
                f"## Case {assignment.assignment_order:02d}",
                "",
                f"- Assignment ID: `{assignment.assignment_id}`",
                f"- Evidence packet ID: `{assignment.example_id}`",
                f"- Packet SHA-256: `{assignment.packet_hash}`",
                "",
                "### Conversation evidence",
                "",
            ]
        )
        for message in packet.conversation_prefix:
            source_id = message.source_message_id or "no-message-id"
            lines.extend(
                [
                    f"**Turn {message.turn_idx} — {message.role} — `{source_id}`**",
                    "",
                ]
            )
            content = getattr(message, "content", None)
            if content is not None:
                lines.extend([_fenced(str(content)), ""])
            tool_calls = getattr(message, "tool_calls", None) or ()
            for call in tool_calls:
                lines.extend(
                    [
                        f"Tool call `{call.id}` — `{call.name}`",
                        "",
                        _fenced(
                            json.dumps(
                                call.arguments,
                                ensure_ascii=False,
                                indent=2,
                                sort_keys=True,
                            )
                        ),
                        "",
                    ]
                )
            if message.role == "tool":
                lines.extend([f"Tool-call evidence ID: `{message.id}`", ""])
        target_id = packet.target_response.source_message_id or "no-message-id"
        lines.extend(
            [
                f"### Target response — `{target_id}`",
                "",
                _fenced(str(packet.target_response.content or "")),
                "",
                "### Blank annotation form",
                "",
            ]
        )
        for criterion in CRITERIA:
            lines.extend(
                [
                    f"**{criterion}**",
                    "",
                    "- Label: ____________________",
                    "- Evidence IDs: ____________________",
                    "- Rationale: ____________________",
                    "- Ambiguity (`true` / `false`): ____________________",
                    "- Elapsed seconds: ____________________",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _entries_by_id(
    entries: Any,
    *,
    context: str,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(entries, list):
        raise RubricDevelopmentPreparationError(f"{context} entries are malformed")
    result: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise RubricDevelopmentPreparationError(
                f"{context} contains a malformed entry"
            )
        example_id = entry.get("example_id")
        if not isinstance(example_id, str) or not example_id or example_id in result:
            raise RubricDevelopmentPreparationError(
                f"{context} contains duplicate or invalid example IDs"
            )
        result[example_id] = entry
    return result


def _fixed_approved_input_paths(
    root: Path,
    *,
    manifest_root: Path,
    private_manifest_path: Path,
    decision_path: Path,
    amendment_path: Path,
    rubric_path: Path,
    normalization_audit_decision_path: Path,
    normalization_audit_resolution_path: Path,
    inventory_path: Path,
) -> tuple[Path, ...]:
    paths = {
        decision_path.resolve(),
        amendment_path.resolve(),
        rubric_path.resolve(),
        normalization_audit_decision_path.resolve(),
        normalization_audit_resolution_path.resolve(),
        inventory_path.resolve(),
        private_manifest_path.resolve(),
        safe_join(root, *HUMAN_AUDIT_REQUEST_RELATIVE).resolve(),
        safe_join(root, "reviews", "G0-request.md").resolve(),
        safe_join(root, "configs", "g0-gpt54.yaml").resolve(),
        safe_join(root, "data", "manifests", "candidate_source_manifest.json").resolve(),
    }
    paths.update(
        (manifest_root / filename).resolve()
        for filename in PUBLIC_MANIFEST_NAMES.values()
    )
    for key, relative in _DECISION_BINDING_PATHS.items():
        paths.add(
            rubric_path.resolve()
            if key == "active_rubric_draft_sha256"
            else safe_join(root, *relative).resolve()
        )
    return tuple(sorted(paths, key=lambda path: path.as_posix()))


def _audit_packet_paths_from_snapshots(
    *,
    packet_root: Path,
    private_manifest: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> tuple[Path, ...]:
    private_entries = _entries_by_id(
        private_manifest.get("entries"),
        context="private packet corpus snapshot",
    )
    example_ids = inventory.get("example_ids")
    if (
        not isinstance(example_ids, list)
        or not example_ids
        or not all(isinstance(item, str) and item for item in example_ids)
        or len(example_ids) != len(set(example_ids))
    ):
        raise RubricDevelopmentPreparationError(
            "normalization-audit inventory snapshot has invalid example IDs"
        )
    paths: list[Path] = []
    for example_id in example_ids:
        entry = private_entries.get(example_id)
        relative = entry.get("packet_path") if entry is not None else None
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise RubricDevelopmentPreparationError(
                f"normalization-audit packet snapshot path is invalid: {example_id}"
            )
        paths.append(safe_join(packet_root, *Path(relative).parts))
    return tuple(paths)


def _assignment_packet_paths(
    ordered: Sequence[Mapping[str, Any]],
    *,
    packet_root: Path,
    private_entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for entry in ordered:
        example_id = entry.get("example_id")
        private = private_entries.get(str(example_id))
        relative = private.get("packet_path") if private is not None else None
        if (
            not isinstance(example_id, str)
            or not example_id
            or example_id in result
            or not isinstance(relative, str)
            or Path(relative).is_absolute()
        ):
            raise RubricDevelopmentPreparationError(
                "rubric-development packet snapshot paths are malformed"
            )
        result[example_id] = safe_join(packet_root, *Path(relative).parts)
    return result


def _capture_file_snapshots(paths: Sequence[Path]) -> dict[Path, _FileSnapshot]:
    snapshots: dict[Path, _FileSnapshot] = {}
    for path in paths:
        snapshot = _snapshot_regular_file(path)
        prior = snapshots.get(snapshot.path)
        if prior is not None and prior.payload != snapshot.payload:
            raise RubricDevelopmentPreparationError(
                f"approved input changed while snapshotting: {snapshot.path.name}"
            )
        snapshots[snapshot.path] = snapshot
    return snapshots


def _snapshot_regular_file(path: Path) -> _FileSnapshot:
    lexical = path.absolute()
    try:
        observed = os.stat(lexical, follow_symlinks=False)
        if not stat.S_ISREG(observed.st_mode):
            raise RubricDevelopmentPreparationError(
                f"approved input must be a regular file: {lexical}"
            )
        descriptor = os.open(lexical, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
                raise RubricDevelopmentPreparationError(
                    f"approved input changed while opening: {lexical}"
                )
            payload = handle.read()
        after = os.stat(lexical, follow_symlinks=False)
    except OSError as error:
        raise RubricDevelopmentPreparationError(
            f"could not snapshot approved input {lexical}: {error}"
        ) from error
    if (
        not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != (observed.st_dev, observed.st_ino)
        or after.st_size != len(payload)
    ):
        raise RubricDevelopmentPreparationError(
            f"approved input changed while snapshotting: {lexical}"
        )
    return _FileSnapshot(
        path=lexical.resolve(),
        payload=payload,
        sha256=sha256_bytes(payload),
    )


def _assert_snapshots_unchanged(snapshots: Sequence[_FileSnapshot]) -> None:
    for expected in snapshots:
        observed = _snapshot_regular_file(expected.path)
        if observed.payload != expected.payload:
            raise RubricDevelopmentPreparationError(
                f"approved input changed before private output publication: "
                f"{expected.path.name}"
            )


def _text_from_snapshot(snapshot: _FileSnapshot, *, context: str) -> str:
    try:
        return snapshot.payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RubricDevelopmentPreparationError(
            f"could not decode {context} as UTF-8: {error}"
        ) from error


def _json_from_snapshot(
    snapshot: _FileSnapshot,
    *,
    context: str,
) -> dict[str, Any]:
    try:
        value = json.loads(_text_from_snapshot(snapshot, context=context))
    except json.JSONDecodeError as error:
        raise RubricDevelopmentPreparationError(
            f"could not decode {context} as JSON: {error}"
        ) from error
    if not isinstance(value, dict):
        raise RubricDevelopmentPreparationError(f"{context} must be a JSON object")
    return value


def _assert_canonical_private_output_is_ignored(root: Path, output_root: Path) -> None:
    resolved_root = root.resolve()
    expected_lexical = resolved_root.joinpath(*ASSIGNMENT_ROOT_RELATIVE)
    if output_root.absolute() != expected_lexical.absolute():
        raise RubricDevelopmentPreparationError(
            "single-reviewer preparation may write only to its private annotation root"
        )
    current = resolved_root
    for part in ASSIGNMENT_ROOT_RELATIVE:
        current = current / part
        if current.is_symlink():
            raise RubricDevelopmentPreparationError(
                "single-reviewer output path cannot contain symlink components"
            )
    try:
        canonical = safe_join(root, *ASSIGNMENT_ROOT_RELATIVE)
    except ValueError as error:
        raise RubricDevelopmentPreparationError(
            "single-reviewer output path escapes the repository"
        ) from error
    if output_root.resolve() != canonical:
        raise RubricDevelopmentPreparationError(
            "single-reviewer preparation may write only to its private annotation root"
        )
    artifact_paths = (
        Path(*ASSIGNMENT_ROOT_RELATIVE, ASSIGNMENT_BUNDLE_NAME).as_posix(),
        Path(*ASSIGNMENT_ROOT_RELATIVE, WORKSHEET_NAME).as_posix(),
    )
    encoded = b"\0".join(path.encode("utf-8") for path in artifact_paths) + b"\0"
    ignored_result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        input=encoded,
    )
    if ignored_result.returncode not in {0, 1}:
        detail = ignored_result.stderr.decode("utf-8", errors="replace").strip()
        raise RubricDevelopmentPreparationError(
            f"could not verify single-reviewer git-ignore coverage: {detail}"
        )
    ignored = {
        value.decode("utf-8")
        for value in ignored_result.stdout.split(b"\0")
        if value
    }
    missing = sorted(set(artifact_paths) - ignored)
    if missing:
        raise RubricDevelopmentPreparationError(
            f"single-reviewer artifacts are not all gitignored: {missing}"
        )
    tracked_result = subprocess.run(
        ["git", "ls-files", "--cached", "-z", "--", *artifact_paths],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if tracked_result.returncode != 0:
        detail = tracked_result.stderr.decode("utf-8", errors="replace").strip()
        raise RubricDevelopmentPreparationError(
            f"could not verify single-reviewer tracked-file status: {detail}"
        )
    tracked = sorted(
        value.decode("utf-8")
        for value in tracked_result.stdout.split(b"\0")
        if value
    )
    if tracked:
        raise RubricDevelopmentPreparationError(
            f"single-reviewer artifacts must not be tracked by git: {tracked}"
        )


def _write_private_outputs(
    output_root: Path,
    expected: Mapping[str, bytes],
    *,
    input_snapshots: Sequence[_FileSnapshot] = (),
) -> None:
    if set(expected) != {ASSIGNMENT_BUNDLE_NAME, WORKSHEET_NAME}:
        raise RubricDevelopmentPreparationError(
            "private preparation output set is not exact"
        )
    with _locked_private_output_directory(output_root) as directory_fd:
        observed_names = set(os.listdir(directory_fd))
        unexpected = observed_names - set(expected)
        if unexpected:
            raise RubricDevelopmentPreparationError(
                "private preparation directory contains unexpected files: "
                f"{sorted(unexpected)}"
            )
        for name, payload in expected.items():
            try:
                observed = _read_regular_at(directory_fd, name)
            except FileNotFoundError:
                observed = None
            if observed is not None and observed != payload:
                raise RubricDevelopmentPreparationError(
                    f"refusing to overwrite drifted private preparation artifact: {name}"
                )
        # Keep this recheck inside the anchored output lock and immediately
        # adjacent to publication so a concurrent approved-input edit fails
        # closed without producing either private artifact.
        _assert_snapshots_unchanged(input_snapshots)
        for name, payload in expected.items():
            _write_new_exact_at(directory_fd, name, payload)


@contextmanager
def _locked_private_output_directory(output_root: Path):
    if output_root.is_symlink():
        raise RubricDevelopmentPreparationError(
            "single-reviewer output root cannot be a symlink"
        )
    if output_root.exists() and not output_root.is_dir():
        raise RubricDevelopmentPreparationError(
            "single-reviewer output root must be a directory"
        )
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd: int | None = None
    try:
        directory_fd = os.open(output_root, flags)
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        anchored = os.fstat(directory_fd)
        observed = os.stat(output_root, follow_symlinks=False)
        if (
            not stat.S_ISDIR(observed.st_mode)
            or (observed.st_dev, observed.st_ino)
            != (anchored.st_dev, anchored.st_ino)
        ):
            raise RubricDevelopmentPreparationError(
                "single-reviewer output directory identity changed"
            )
        yield directory_fd
    except OSError as error:
        raise RubricDevelopmentPreparationError(
            f"could not anchor single-reviewer output safely: {error}"
        ) from error
    finally:
        if directory_fd is not None:
            fcntl.flock(directory_fd, fcntl.LOCK_UN)
            os.close(directory_fd)


def _write_new_exact_at(directory_fd: int, name: str, payload: bytes) -> None:
    try:
        observed = _read_regular_at(directory_fd, name)
    except FileNotFoundError:
        observed = None
    if observed is not None:
        if observed != payload:
            raise RubricDevelopmentPreparationError(
                f"refusing to overwrite drifted private preparation artifact: {name}"
            )
        return

    temporary_name = f".{name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            observed = _read_regular_at(directory_fd, name)
            if observed != payload:
                raise RubricDevelopmentPreparationError(
                    f"refusing concurrent output drift: {name}"
                ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _read_regular_at(directory_fd: int, name: str) -> bytes:
    observed = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(observed.st_mode):
        raise RubricDevelopmentPreparationError(
            f"private preparation artifact must be a regular file: {name}"
        )
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
            raise RubricDevelopmentPreparationError(
                f"private preparation artifact changed while opening: {name}"
            )
        return handle.read()


def _is_timezone_aware_iso8601(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _fenced(value: str) -> str:
    # Use a tilde fence so ordinary JSON/Markdown backticks cannot close it.
    fence = "~~~~"
    while fence in value:
        fence += "~"
    return f"{fence}text\n{value}\n{fence}"


def _display_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(root):
        return resolved.relative_to(root).as_posix()
    return resolved.as_posix()
