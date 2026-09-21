from __future__ import annotations

import inspect
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

import judge_compare.rubric_development as rubric_development
from judge_compare.annotation import BlindedAnnotationAssignment
from judge_compare.hashing import canonical_sha256, sha256_file
from judge_compare.m1 import (
    HUMAN_AUDIT_INVENTORY_RELATIVE,
    HUMAN_AUDIT_REQUEST_RELATIVE,
    HUMAN_AUDIT_REQUIRED_CHECKS,
    HUMAN_AUDIT_REQUIRED_COVERAGE,
    PUBLIC_MANIFEST_NAMES,
    _build_normalization_audit_resolution,
    _load_and_validate_m1_proposals,
    _validate_bound_private_corpus,
    _validate_human_normalization_audit_decision,
    _validate_human_normalization_audit_request,
    _validate_reviewed_id_inventory,
)
from judge_compare.models import EvidencePacket
from judge_compare.rubric_development import (
    _DECISION_BINDING_PATHS,
    _DECISION_SCOPE,
    ASSIGNMENT_BUNDLE_NAME,
    CALIBRATION_CASES,
    SINGLE_REVIEWER_AMENDMENT_RELATIVE,
    WORKSHEET_NAME,
    RubricDevelopmentPreparationError,
    _assert_canonical_private_output_is_ignored,
    _build_assignments,
    _prepare_single_reviewer_rubric_development_internal,
    _snapshot_regular_file,
    _write_private_outputs,
    prepare_single_reviewer_rubric_development,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_ROOT = ROOT / "data" / "manifests"
PACKET_ROOT = ROOT / "data" / "packets" / "v1"
RUBRIC_PATH = ROOT / "protocol" / "rubric-draft.md"


@dataclass(frozen=True)
class _PreparedInputs:
    output_root: Path
    decision_path: Path
    amendment_path: Path
    audit_decision_path: Path
    audit_resolution_path: Path


def _load_proposals():
    request_path = ROOT.joinpath(*HUMAN_AUDIT_REQUEST_RELATIVE)
    request = _validate_human_normalization_audit_request(request_path)
    proposals = _load_and_validate_m1_proposals(
        ROOT,
        manifest_root=MANIFEST_ROOT,
        request_summary=request,
    )
    private_path = PACKET_ROOT / "corpus_manifest.json"
    private = _validate_bound_private_corpus(
        ROOT,
        packet_root=PACKET_ROOT,
        private_manifest_path=private_path,
        corpus_proposal=proposals["corpus"],
    )
    return request_path, request, proposals, private_path, private


def _write_valid_inputs(tmp_path: Path) -> _PreparedInputs:
    tmp_path.mkdir(parents=True, exist_ok=True)
    request_path, request, proposals, private_path, private = _load_proposals()
    inventory_path = PACKET_ROOT.joinpath(*HUMAN_AUDIT_INVENTORY_RELATIVE)
    audit_decision = {
        "schema_version": "1.0",
        "gate": "M1-normalization-audit",
        "artifact_type": "human_audit_decision",
        "decision": "approved",
        "reviewer": "Ayush",
        "reviewed_at": "2026-09-21T12:00:00Z",
        "conditions": [],
        "audit_request_sha256": sha256_file(request_path),
        "private_packet_corpus_manifest_hash": private["manifest_hash"],
        "proposal_manifest_hashes": {
            key: proposals[key]["manifest_hash"] for key in PUBLIC_MANIFEST_NAMES
        },
        "reviewed_example_ids_inventory_sha256": sha256_file(inventory_path),
        "packets_reviewed": 10,
        "coverage": {key: True for key in HUMAN_AUDIT_REQUIRED_COVERAGE},
        "checks": {key: True for key in HUMAN_AUDIT_REQUIRED_CHECKS},
        "notes": "Synthetic approved decision used only by the isolated test seam.",
    }
    audit_decision_path = tmp_path / "normalization-audit-decision.yaml"
    audit_decision_path.write_text(
        yaml.safe_dump(audit_decision, sort_keys=False),
        encoding="utf-8",
    )
    validated_audit_decision = _validate_human_normalization_audit_decision(
        audit_decision_path,
        request_sha256=request["request_sha256"],
        private_manifest_hash=private["manifest_hash"],
        proposal_manifest_hashes={
            key: proposals[key]["manifest_hash"] for key in PUBLIC_MANIFEST_NAMES
        },
    )
    inventory_sha256, reviewed_ids = _validate_reviewed_id_inventory(
        inventory_path,
        expected_sha256=validated_audit_decision[
            "reviewed_example_ids_inventory_sha256"
        ],
        expected_private_manifest_hash=private["manifest_hash"],
        expected_proposal_corpus_hash=proposals["corpus"]["manifest_hash"],
        expected_count=10,
        packet_root=PACKET_ROOT,
        private_manifest=private,
        corpus_proposal=proposals["corpus"],
        pool_proposal=proposals["pool"],
    )
    audit_resolution = _build_normalization_audit_resolution(
        root=ROOT,
        request_path=request_path,
        decision_path=audit_decision_path,
        decision=validated_audit_decision,
        inventory_sha256=inventory_sha256,
        private_manifest_path=private_path,
        private_manifest=private,
        manifest_root=MANIFEST_ROOT,
        proposals=proposals,
        reviewed_count=len(reviewed_ids),
    )
    audit_resolution_path = tmp_path / "normalization-audit-resolution.json"
    audit_resolution_path.write_text(
        json.dumps(audit_resolution, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    amendment_path = tmp_path / "single-reviewer-annotation-amendment.md"
    amendment_path.write_text(
        "# Test-only single-reviewer amendment\n\nAyush is the sole reviewer.\n",
        encoding="utf-8",
    )
    decision_bindings = {
        key: sha256_file(ROOT.joinpath(*relative))
        for key, relative in _DECISION_BINDING_PATHS.items()
    }
    pool_path = MANIFEST_ROOT / PUBLIC_MANIFEST_NAMES["pool"]
    decision_bindings.update(
        {
            "m1_pool_manifest_file_sha256": sha256_file(pool_path),
            "m1_pool_manifest_hash": proposals["pool"]["manifest_hash"],
        }
    )
    decision = {
        "schema_version": "1.0",
        "gate": "annotation-plan-amendment",
        "decision": "approved",
        "reviewer": "Ayush",
        "reviewed_at": "2026-09-21T13:00:00Z",
        "conditions": [],
        "amendment_path": Path(*SINGLE_REVIEWER_AMENDMENT_RELATIVE).as_posix(),
        "amendment_sha256": sha256_file(amendment_path),
        "bindings": decision_bindings,
        "scope": dict(_DECISION_SCOPE),
    }
    decision_path = tmp_path / "single-reviewer-annotation-decision.yaml"
    decision_path.write_text(
        yaml.safe_dump(decision, sort_keys=False),
        encoding="utf-8",
    )
    return _PreparedInputs(
        output_root=tmp_path / "private-preparation",
        decision_path=decision_path,
        amendment_path=amendment_path,
        audit_decision_path=audit_decision_path,
        audit_resolution_path=audit_resolution_path,
    )


def _prepare(inputs: _PreparedInputs):
    return _prepare_single_reviewer_rubric_development_internal(
        ROOT,
        manifest_root=MANIFEST_ROOT,
        packet_root=PACKET_ROOT,
        output_root=inputs.output_root,
        decision_path=inputs.decision_path,
        amendment_path=inputs.amendment_path,
        rubric_path=RUBRIC_PATH,
        normalization_audit_decision_path=inputs.audit_decision_path,
        normalization_audit_resolution_path=inputs.audit_resolution_path,
    )


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def test_single_reviewer_preparation_is_blinded_unlabeled_and_calibration_only(
    tmp_path: Path,
) -> None:
    inputs = _write_valid_inputs(tmp_path)
    result = _prepare(inputs)
    bundle = result.bundle
    pool = json.loads(
        (MANIFEST_ROOT / PUBLIC_MANIFEST_NAMES["pool"]).read_text(encoding="utf-8")
    )
    calibration_ids = {
        item["example_id"] for item in pool["annotation_calibration"]
    }
    model_ids = {item["example_id"] for item in pool["model_judgment"]}
    model_families = {
        item["task_family_id"] for item in pool["model_judgment"]
    }

    assignments = [
        BlindedAnnotationAssignment.model_validate(item)
        for item in bundle["assignments"]
    ]
    assigned_ids = {item.example_id for item in assignments}
    assert len(assignments) == CALIBRATION_CASES
    assert assigned_ids == calibration_ids
    assert not (assigned_ids & model_ids)
    assert bundle["status"] == "prepared_unlabeled_rubric_development_only"
    assert bundle["reviewer"] == "Ayush"
    assert bundle["reviewer_count"] == 1
    assert bundle["labels_included"] is False
    assert bundle["model_calls_made"] == 0
    assert bundle["network_access_used"] is False
    assert bundle["counts"]["labels_produced"] == 0
    assert bundle["pool_boundary"] == {
        "rubric_development_assignments": 24,
        "model_judgment_cases_maximum": 21,
        "model_judgment_assignments_prepared": 0,
        "model_judgment_case_identifiers_exposed": 0,
        "model_judgment_packet_contents_exposed": False,
    }
    assert bundle["single_reviewer_reference_contract"] == {
        "record_type": "raw_annotation_submission",
        "submissions_required_per_assignment": 1,
        "final_reference": "validated_immutable_raw_annotation_submission",
        "adjudicated_annotation_record_created": False,
        "inter_rater_metrics_computed": False,
    }
    assert "task_family_id" not in _all_keys(bundle)
    bundle_text = result.bundle_path.read_text(encoding="utf-8")
    worksheet = result.worksheet_path.read_text(encoding="utf-8")
    assert all(example_id not in bundle_text for example_id in model_ids)
    assert all(example_id not in worksheet for example_id in model_ids)
    assert all(family not in bundle_text for family in model_families)
    assert all(family not in worksheet for family in model_families)
    assert "PREPARED ONLY — UNLABELED" in worksheet
    assert "The 21 model-judgment cases are not exposed or prepared here." in worksheet
    assert "`protocol/rubric-draft.md`" in worksheet
    assert "`protocol/single-reviewer-annotation-amendment.md`" in worksheet
    assert "supersedes its two-reviewer" in worksheet
    assert "## Bound criterion rubric" in worksheet
    assert "Criterion 1: grounding" in worksheet


def test_order_is_deterministic_and_not_the_m1_selection_order(tmp_path: Path) -> None:
    inputs = _write_valid_inputs(tmp_path)
    first = _prepare(inputs)
    original_order = [
        item["example_id"]
        for item in json.loads(
            (MANIFEST_ROOT / PUBLIC_MANIFEST_NAMES["pool"]).read_text(
                encoding="utf-8"
            )
        )["annotation_calibration"]
    ]
    randomized = [item["example_id"] for item in first.bundle["assignments"]]
    assert randomized != original_order

    bundle_inode = first.bundle_path.stat().st_ino
    worksheet_inode = first.worksheet_path.stat().st_ino
    second = _prepare(inputs)
    assert second.bundle == first.bundle
    assert second.bundle_path.stat().st_ino == bundle_inode
    assert second.worksheet_path.stat().st_ino == worksheet_inode


def test_preparation_refuses_output_drift(tmp_path: Path) -> None:
    inputs = _write_valid_inputs(tmp_path)
    result = _prepare(inputs)
    result.worksheet_path.write_text(
        result.worksheet_path.read_text(encoding="utf-8") + "drift\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RubricDevelopmentPreparationError,
        match="refusing to overwrite drifted",
    ):
        _prepare(inputs)


def test_preparation_rejects_scope_or_binding_drift(tmp_path: Path) -> None:
    inputs = _write_valid_inputs(tmp_path)
    decision = yaml.safe_load(inputs.decision_path.read_text(encoding="utf-8"))
    decision["scope"]["reviewer_count"] = 2
    inputs.decision_path.write_text(
        yaml.safe_dump(decision, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RubricDevelopmentPreparationError, match="scope is not"):
        _prepare(inputs)

    inputs = _write_valid_inputs(tmp_path / "binding")
    decision = yaml.safe_load(inputs.decision_path.read_text(encoding="utf-8"))
    decision["bindings"]["active_rubric_draft_sha256"] = "0" * 64
    inputs.decision_path.write_text(
        yaml.safe_dump(decision, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(RubricDevelopmentPreparationError, match="binding mismatch"):
        _prepare(inputs)


def test_preparation_requires_completed_normalization_audit(tmp_path: Path) -> None:
    inputs = _write_valid_inputs(tmp_path)
    missing = tmp_path / "missing-resolution.json"
    inputs = _PreparedInputs(
        output_root=inputs.output_root,
        decision_path=inputs.decision_path,
        amendment_path=inputs.amendment_path,
        audit_decision_path=inputs.audit_decision_path,
        audit_resolution_path=missing,
    )
    with pytest.raises(
        RubricDevelopmentPreparationError,
        match=r"require.*normalization-audit resolution",
    ):
        _prepare(inputs)
    assert not inputs.output_root.exists()


def test_normalization_audit_must_be_signed_by_ayush(tmp_path: Path) -> None:
    inputs = _write_valid_inputs(tmp_path)
    audit_decision = yaml.safe_load(
        inputs.audit_decision_path.read_text(encoding="utf-8")
    )
    audit_decision["reviewer"] = "AnotherReviewer"
    inputs.audit_decision_path.write_text(
        yaml.safe_dump(audit_decision, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(
        RubricDevelopmentPreparationError,
        match="approved sole human reviewer, Ayush",
    ):
        _prepare(inputs)
    assert not inputs.output_root.exists()


def test_public_preparer_has_no_output_override_and_targets_ignored_files() -> None:
    assert set(inspect.signature(prepare_single_reviewer_rubric_development).parameters) == {
        "root"
    }
    for name in (ASSIGNMENT_BUNDLE_NAME, WORKSHEET_NAME):
        relative = Path(
            "annotations",
            "rubric-development",
            "v1",
            "preparation",
            name,
        )
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", relative.as_posix()],
            cwd=ROOT,
            check=False,
        )
        assert ignored.returncode == 0


def test_private_output_guard_rejects_symlinked_ancestor_and_tracked_targets(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".gitignore").write_text("annotations/**/*\n", encoding="utf-8")
    canonical = tmp_path.joinpath(
        "annotations",
        "rubric-development",
        "v1",
        "preparation",
    )
    _assert_canonical_private_output_is_ignored(tmp_path, canonical)

    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "annotations").symlink_to(outside, target_is_directory=True)
    with pytest.raises(
        RubricDevelopmentPreparationError,
        match="symlink components",
    ):
        _assert_canonical_private_output_is_ignored(tmp_path, canonical)

    (tmp_path / "annotations").unlink()
    canonical.mkdir(parents=True)
    tracked = canonical / ASSIGNMENT_BUNDLE_NAME
    tracked.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", tracked.relative_to(tmp_path).as_posix()],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    with pytest.raises(RubricDevelopmentPreparationError, match="must not be tracked"):
        _assert_canonical_private_output_is_ignored(tmp_path, canonical)


def test_private_writer_rejects_symlink_and_nonregular_targets(tmp_path: Path) -> None:
    expected = {
        ASSIGNMENT_BUNDLE_NAME: b"bundle\n",
        WORKSHEET_NAME: b"worksheet\n",
    }
    output = tmp_path / "private"
    output.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside\n", encoding="utf-8")
    (output / ASSIGNMENT_BUNDLE_NAME).symlink_to(target)
    with pytest.raises(RubricDevelopmentPreparationError, match="regular file"):
        _write_private_outputs(output, expected)
    assert target.read_text(encoding="utf-8") == "outside\n"

    (output / ASSIGNMENT_BUNDLE_NAME).unlink()
    (output / ASSIGNMENT_BUNDLE_NAME).mkdir()
    with pytest.raises(RubricDevelopmentPreparationError, match="regular file"):
        _write_private_outputs(output, expected)

    linked_root = tmp_path / "linked-private"
    linked_root.symlink_to(output, target_is_directory=True)
    with pytest.raises(RubricDevelopmentPreparationError, match="root cannot be a symlink"):
        _write_private_outputs(linked_root, expected)


def test_private_writer_detects_input_mutation_at_locked_recheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved_input = tmp_path / "approved-input.md"
    approved_input.write_text("approved\n", encoding="utf-8")
    snapshot = _snapshot_regular_file(approved_input)
    output = tmp_path / "private"
    expected = {
        ASSIGNMENT_BUNDLE_NAME: b"bundle\n",
        WORKSHEET_NAME: b"worksheet\n",
    }
    original_recheck = rubric_development._assert_snapshots_unchanged

    def mutate_then_recheck(snapshots):
        approved_input.write_text("changed during build\n", encoding="utf-8")
        original_recheck(snapshots)

    monkeypatch.setattr(
        rubric_development,
        "_assert_snapshots_unchanged",
        mutate_then_recheck,
    )
    with pytest.raises(
        RubricDevelopmentPreparationError,
        match="changed before private output publication",
    ):
        _write_private_outputs(output, expected, input_snapshots=(snapshot,))
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_assignment_builder_rejects_noncanonical_packet_json(tmp_path: Path) -> None:
    packet_value = {
        "example_id": "opaque-calibration-case",
        "policy": "Use visible tool evidence.",
        "tool_definitions": [
            {
                "name": "lookup",
                "description": "Look up an item.",
                "parameters": {
                    "type": "object",
                    "title": "parameters",
                    "properties": {},
                },
            }
        ],
        "conversation_prefix": [
            {
                "role": "user",
                "content": "Where is my item?",
                "turn_idx": 0,
            }
        ],
        "target_response": {
            "content": "I cannot determine that from the visible evidence.",
            "turn_idx": 1,
        },
    }
    # The omitted default fields are accepted by Pydantic but do not equal the
    # canonical model dump; the assignment path must reject this drift.
    packet_dir = tmp_path / "packets"
    packet_dir.mkdir()
    packet_path = packet_dir / "case.json"
    packet_path.write_text(json.dumps(packet_value), encoding="utf-8")
    parsed = EvidencePacket.model_validate(packet_value)
    canonical = parsed.model_dump(mode="json")
    packet_hash = canonical_sha256(canonical)
    entry = {
        "example_id": "opaque-calibration-case",
        "task_id": "1",
        "trial": 0,
        "packet_hash": packet_hash,
        "packet_size_bytes": len(
            json.dumps(
                canonical,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ),
        "task_family_id": "family-calibration",
    }
    private_entry = dict(entry, packet_path="packets/case.json")
    with pytest.raises(RubricDevelopmentPreparationError, match="not canonical"):
        _build_assignments(
            [entry],
            packet_root=tmp_path,
            public_entries={entry["example_id"]: entry},
            private_entries={entry["example_id"]: private_entry},
            rubric_hash="a" * 64,
            forbidden_model_ids=frozenset(),
            forbidden_model_families=frozenset(),
        )


def test_rubric_development_import_loads_no_network_or_model_client() -> None:
    command = (
        "import sys; import judge_compare.rubric_development; "
        "blocked={'httpx','openai','typesafe_sdk','typesafe'}; "
        "loaded=blocked.intersection(sys.modules); assert not loaded, loaded"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
