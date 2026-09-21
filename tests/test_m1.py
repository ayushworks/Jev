from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from judge_compare.hashing import canonical_json_bytes, canonical_sha256, sha256_file
from judge_compare.m1 import (
    HUMAN_AUDIT_INVENTORY_RELATIVE,
    HUMAN_AUDIT_RESOLUTION_NAME,
    PRIVATE_PUBLIC_KEYS,
    PUBLIC_MANIFEST_NAMES,
    M1PreparationError,
    _assert_canonical_packet_root_is_ignored,
    _preflight_public_outputs,
    _prepare_m1_internal,
    _record_m1_normalization_audit_internal,
    _validate_timing_preview_hash,
    prepare_m1,
    record_m1_normalization_audit,
)

ROOT = Path(__file__).resolve().parents[1]


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(child) for child in value))
    return set()


def _assert_manifest_hash(manifest: dict) -> None:
    content = dict(manifest)
    recorded = content.pop("manifest_hash")
    assert recorded == canonical_sha256(content)


@pytest.fixture(scope="module")
def prepared_m1(tmp_path_factory: pytest.TempPathFactory):
    output = tmp_path_factory.mktemp("m1-preparation")
    result = _prepare_m1_internal(
        ROOT,
        packet_root=output / "packets",
        manifest_root=output / "manifests",
    )
    return result


def test_prepare_m1_builds_exact_offline_frame_and_provisional_pools(prepared_m1) -> None:
    result = prepared_m1
    corpus = result.corpus_manifest
    split = result.split_manifest
    pool = result.pool_manifest
    exclusion = result.exclusion_manifest

    for manifest in (corpus, split, pool, exclusion):
        _assert_manifest_hash(manifest)
        assert not (_all_keys(manifest) & PRIVATE_PUBLIC_KEYS)

    assert corpus["model_calls_made"] == 0
    assert corpus["network_access_used"] is False
    assert corpus["counts"] == {
        "source_trajectories": 456,
        "canonical_packets": 456,
        "owner_exposure_exclusions": 8,
        "structurally_eligible_after_exposure_exclusion": 448,
        "exact_context_eligible": None,
        "exact_context_eligibility_pending": 448,
    }
    assert corpus["packet_size_metric"] == "canonical_json_utf8_bytes"
    assert corpus["context_eligibility"]["status"] == "not_established_offline"
    blocker = corpus["g1_blockers"][0]
    assert blocker["blocker_id"] == "exact_context_token_eligibility"
    assert blocker["final_pool_freeze_allowed"] is False
    assert blocker["token_count_api_calls_made"] == 0
    assert "/v1/responses/input_tokens" in blocker["required_resolution"]
    assert "approved amendment" in blocker["required_resolution"]
    assert "rerun" not in blocker["required_resolution"]
    human_blocker = corpus["g1_blockers"][1]
    assert human_blocker["blocker_id"] == "human_source_normalization_audit"
    assert human_blocker["minimum_packets"] == 10
    assert human_blocker["human_signoff_present"] is False
    assert human_blocker["required_sample_rule"] == {
        "split": "test",
        "minimum_distinct_task_families": 10,
        "provisional_pool_family_overlap": 0,
        "timing_exposed_family_overlap": 0,
    }
    assert corpus["human_normalization_audit"]["status"] == (
        "open_at_proposal_time"
    )
    assert corpus["human_normalization_audit"]["complete"] is False
    assert corpus["human_normalization_audit"]["request_path"] == (
        "reviews/M1-normalization-audit-request.yaml"
    )
    assert human_blocker["audit_request_sha256"] == (
        corpus["human_normalization_audit"]["request_sha256"]
    )
    assert corpus["status"] == "offline_packet_build_validated_m1_not_final"
    assert corpus["source_reconciliation"] == {
        "equation": "456 source = 448 structurally eligible + 8 exposure-excluded",
        "source_trajectories": 456,
        "structurally_eligible": 448,
        "exposure_excluded": 8,
        "reconciled": True,
    }

    assert split["family_count"] == 72
    assert split["family_counts_by_split"] == {"development": 18, "test": 54}
    assert split["eligible_family_counts_after_owner_exposure"] == {
        "development": 17,
        "test": 54,
    }
    assert split["cross_split_family_overlap_count"] == 0

    assert pool["final_pool_freeze"] is False
    assert pool["status"] == "provisional_not_final_g1_blockers_open"
    assert len(pool["g1_blockers"]) == 2
    assert pool["counts"] == {
        "model_judgment_cases": 21,
        "model_judgment_development_cases": 5,
        "model_judgment_test_cases": 16,
        "model_judgment_families": 21,
        "annotation_calibration_cases": 24,
        "annotation_calibration_families": 12,
        "case_overlap": 0,
        "family_overlap": 0,
    }
    assert pool["outcome_fields_used"] == []
    assert pool["repeatability"]["status"] == "not_applicable"
    assert pool["repeatability"]["selected_packets"] == 0

    model_ids = {case["example_id"] for case in pool["model_judgment"]}
    calibration_ids = {
        case["example_id"] for case in pool["annotation_calibration"]
    }
    model_families = {
        case["task_family_id"] for case in pool["model_judgment"]
    }
    calibration_families = {
        case["task_family_id"] for case in pool["annotation_calibration"]
    }
    assert not (model_ids & calibration_ids)
    assert not (model_families & calibration_families)
    assert "retail-task-family-096" not in model_families | calibration_families
    assert {case["split"] for case in pool["annotation_calibration"]} == {
        "development"
    }

    assert exclusion["counts"]["excluded_families"] == 1
    assert exclusion["counts"]["excluded_cases"] == 8
    assert {entry["task_family_id"] for entry in exclusion["entries"]} == {
        "retail-task-family-096"
    }
    assert {entry["task_id"] for entry in exclusion["entries"]} == {"96", "97"}
    assert exclusion["bindings"]["annotation_timing_preview_sha256"] == (
        "0168faf5d012a19448da74b8e13f25b937e76dfff5fb399854e7ba60494dba9a"
    )
    assert "reward" not in _all_keys(corpus)
    assert "reward" not in _all_keys(pool)


def test_prepare_m1_materializes_private_traceability_only_under_packet_root(
    prepared_m1,
) -> None:
    result = prepared_m1
    packet_files = sorted((result.packet_root / "packets").glob("*.json"))
    assert len(packet_files) == 456
    private_manifest = json.loads(
        (result.packet_root / "corpus_manifest.json").read_text(encoding="utf-8")
    )
    assert len(private_manifest["entries"]) == 456
    assert "private_traceability" in private_manifest["entries"][0]

    packet = json.loads(packet_files[0].read_text(encoding="utf-8"))
    public_entry = next(
        entry
        for entry in result.corpus_manifest["entries"]
        if entry["example_id"] == packet["example_id"]
    )
    assert canonical_sha256(packet) == public_entry["packet_hash"]
    assert len(canonical_json_bytes(packet)) == public_entry["packet_size_bytes"]
    assert Path(public_entry["packet_path"]).resolve() == packet_files[0].resolve()
    assert Path(result.corpus_manifest["private_traceability_location"]).resolve() == (
        result.packet_root / "corpus_manifest.json"
    ).resolve()

    for filename in PUBLIC_MANIFEST_NAMES.values():
        public = json.loads((result.manifest_root / filename).read_text(encoding="utf-8"))
        assert not (_all_keys(public) & PRIVATE_PUBLIC_KEYS)


def test_prepare_m1_is_idempotent(prepared_m1) -> None:
    proposal_bytes = {
        key: (prepared_m1.manifest_root / filename).read_bytes()
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    proposal_inodes = {
        key: (prepared_m1.manifest_root / filename).stat().st_ino
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    repeated = _prepare_m1_internal(
        ROOT,
        packet_root=prepared_m1.packet_root,
        manifest_root=prepared_m1.manifest_root,
    )

    assert repeated.corpus_manifest == prepared_m1.corpus_manifest
    assert repeated.split_manifest == prepared_m1.split_manifest
    assert repeated.pool_manifest == prepared_m1.pool_manifest
    assert repeated.exclusion_manifest == prepared_m1.exclusion_manifest
    assert {
        key: (prepared_m1.manifest_root / filename).read_bytes()
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    } == proposal_bytes
    assert {
        key: (prepared_m1.manifest_root / filename).stat().st_ino
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    } == proposal_inodes


def test_public_prepare_m1_has_no_arbitrary_output_root_and_canonical_root_is_ignored(
    tmp_path: Path,
) -> None:
    assert set(inspect.signature(prepare_m1).parameters) == {"root"}
    assert set(inspect.signature(record_m1_normalization_audit).parameters) == {
        "root"
    }
    canonical = ROOT / "data" / "packets" / "v1"
    _assert_canonical_packet_root_is_ignored(ROOT, canonical)

    with pytest.raises(M1PreparationError, match="only under data/packets/v1"):
        _assert_canonical_packet_root_is_ignored(ROOT, tmp_path / "packets")


def test_canonical_packet_root_requires_both_private_output_shapes_ignored(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / ".gitignore").write_text(
        "data/packets/v1/corpus_manifest.json\n",
        encoding="utf-8",
    )

    with pytest.raises(M1PreparationError, match="manifest, packet, and audit files"):
        _assert_canonical_packet_root_is_ignored(
            tmp_path,
            tmp_path / "data" / "packets" / "v1",
        )


def _approved_audit_decision(
    prepared,
    *,
    inventory_sha256: str,
) -> dict:
    private_manifest = json.loads(
        (prepared.packet_root / "corpus_manifest.json").read_text(encoding="utf-8")
    )
    proposals = {
        key: json.loads(
            (prepared.manifest_root / filename).read_text(encoding="utf-8")
        )
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    return {
        "schema_version": "1.0",
        "gate": "M1-normalization-audit",
        "artifact_type": "human_audit_decision",
        "decision": "approved",
        "reviewer": "Human Reviewer",
        "reviewed_at": "2026-09-21T12:00:00Z",
        "conditions": [],
        "audit_request_sha256": sha256_file(
            ROOT / "reviews" / "M1-normalization-audit-request.yaml"
        ),
        "private_packet_corpus_manifest_hash": private_manifest["manifest_hash"],
        "proposal_manifest_hashes": {
            key: proposals[key]["manifest_hash"] for key in PUBLIC_MANIFEST_NAMES
        },
        "reviewed_example_ids_inventory_sha256": inventory_sha256,
        "packets_reviewed": 10,
        "coverage": {
            "short_packet": True,
            "long_packet": True,
            "source_success_case": True,
            "source_failure_case": True,
        },
        "checks": {
            "conversation_order_matches_source": True,
            "target_response_matches_source": True,
            "terminal_future_message_excluded": True,
            "tool_calls_and_results_preserved_and_paired": True,
            "policy_and_tool_definitions_match_pinned_source": True,
            "forbidden_fields_absent": True,
        },
        "notes": "Test-only approved decision.",
    }


def test_normalization_audit_resolution_is_append_only_and_keeps_proposals_immutable(
    tmp_path: Path,
) -> None:
    prepared = _prepare_m1_internal(
        ROOT,
        packet_root=tmp_path / "packets",
        manifest_root=tmp_path / "manifests",
    )
    proposal_paths = {
        key: prepared.manifest_root / filename
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    proposal_bytes = {key: path.read_bytes() for key, path in proposal_paths.items()}
    decision_path = tmp_path / "M1-normalization-audit-decision.yaml"
    pending = yaml.safe_load(
        (ROOT / "reviews" / "M1-normalization-audit-decision.yaml").read_text(
            encoding="utf-8"
        )
    )
    decision_path.write_text(
        yaml.safe_dump(pending, sort_keys=False),
        encoding="utf-8",
    )
    inventory_path = prepared.packet_root.joinpath(
        *HUMAN_AUDIT_INVENTORY_RELATIVE
    )
    resolution_path = prepared.manifest_root / HUMAN_AUDIT_RESOLUTION_NAME

    with pytest.raises(M1PreparationError, match="not approved and valid"):
        _record_m1_normalization_audit_internal(
            ROOT,
            packet_root=prepared.packet_root,
            manifest_root=prepared.manifest_root,
            decision_path=decision_path,
            inventory_path=inventory_path,
        )
    assert not resolution_path.exists()

    private_manifest = json.loads(
        (prepared.packet_root / "corpus_manifest.json").read_text(encoding="utf-8")
    )
    selected_families = {
        entry["task_family_id"]
        for entry in [
            *prepared.pool_manifest["model_judgment"],
            *prepared.pool_manifest["annotation_calibration"],
        ]
    }
    reviewed_ids = []
    reviewed_families: set[str] = set()
    for entry in prepared.corpus_manifest["entries"]:
        family_id = entry["task_family_id"]
        if (
            entry["split"] == "test"
            and family_id not in selected_families
            and family_id != "retail-task-family-096"
            and family_id not in reviewed_families
        ):
            reviewed_ids.append(entry["example_id"])
            reviewed_families.add(family_id)
        if len(reviewed_ids) == 10:
            break
    assert len(reviewed_ids) == len(reviewed_families) == 10
    inventory = {
        "schema_version": "1.0",
        "inventory_type": "m1_normalization_audit_reviewed_examples",
        "private_packet_corpus_manifest_hash": private_manifest["manifest_hash"],
        "proposal_corpus_manifest_hash": prepared.corpus_manifest["manifest_hash"],
        "example_ids": reviewed_ids,
    }
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approved = _approved_audit_decision(
        prepared,
        inventory_sha256=sha256_file(inventory_path),
    )

    approved["coverage"]["source_failure_case"] = False
    decision_path.write_text(
        yaml.safe_dump(approved, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(M1PreparationError, match="required length/outcome coverage"):
        _record_m1_normalization_audit_internal(
            ROOT,
            packet_root=prepared.packet_root,
            manifest_root=prepared.manifest_root,
            decision_path=decision_path,
            inventory_path=inventory_path,
        )
    assert not resolution_path.exists()

    approved["coverage"]["source_failure_case"] = True
    decision_path.write_text(
        yaml.safe_dump(approved, sort_keys=False),
        encoding="utf-8",
    )
    original_inventory_bytes = inventory_path.read_bytes()
    inventory_path.write_bytes(original_inventory_bytes + b" ")
    with pytest.raises(M1PreparationError, match="inventory hash does not match"):
        _record_m1_normalization_audit_internal(
            ROOT,
            packet_root=prepared.packet_root,
            manifest_root=prepared.manifest_root,
            decision_path=decision_path,
            inventory_path=inventory_path,
        )
    assert not resolution_path.exists()
    inventory_path.write_bytes(original_inventory_bytes)

    contaminated_inventory = dict(inventory)
    contaminated_inventory["example_ids"] = [
        prepared.pool_manifest["model_judgment"][0]["example_id"],
        *reviewed_ids[1:],
    ]
    inventory_path.write_text(
        json.dumps(
            contaminated_inventory,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    approved["reviewed_example_ids_inventory_sha256"] = sha256_file(inventory_path)
    decision_path.write_text(
        yaml.safe_dump(approved, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(M1PreparationError, match="distinct unselected test families"):
        _record_m1_normalization_audit_internal(
            ROOT,
            packet_root=prepared.packet_root,
            manifest_root=prepared.manifest_root,
            decision_path=decision_path,
            inventory_path=inventory_path,
        )
    assert not resolution_path.exists()

    inventory_path.write_bytes(original_inventory_bytes)
    approved["reviewed_example_ids_inventory_sha256"] = sha256_file(inventory_path)
    decision_path.write_text(
        yaml.safe_dump(approved, sort_keys=False),
        encoding="utf-8",
    )

    recorded = _record_m1_normalization_audit_internal(
        ROOT,
        packet_root=prepared.packet_root,
        manifest_root=prepared.manifest_root,
        decision_path=decision_path,
        inventory_path=inventory_path,
    )
    _assert_manifest_hash(recorded.manifest)
    assert recorded.manifest["status"] == (
        "human_audit_resolved_m1_still_not_final"
    )
    assert recorded.manifest["review"]["packets_reviewed"] == 10
    assert recorded.manifest["review"]["sample_blinding"] == {
        "split": "test",
        "distinct_unselected_task_families": 10,
        "provisional_pool_family_overlap": 0,
        "timing_exposed_family_overlap": 0,
    }
    assert recorded.manifest["remaining_g1_blockers"] == [
        "exact_context_token_eligibility"
    ]
    assert recorded.manifest["m1_final_promotion_allowed"] is False
    assert "example_ids" not in _all_keys(recorded.manifest)
    assert "reward" not in _all_keys(recorded.manifest)
    for key, path in proposal_paths.items():
        assert Path(
            recorded.manifest["bindings"]["proposal_manifests"][key]["path"]
        ).resolve() == path.resolve()
    assert {key: path.read_bytes() for key, path in proposal_paths.items()} == (
        proposal_bytes
    )

    resolution_bytes = resolution_path.read_bytes()
    resolution_inode = resolution_path.stat().st_ino
    repeated = _record_m1_normalization_audit_internal(
        ROOT,
        packet_root=prepared.packet_root,
        manifest_root=prepared.manifest_root,
        decision_path=decision_path,
        inventory_path=inventory_path,
    )
    assert repeated.manifest == recorded.manifest
    assert resolution_path.read_bytes() == resolution_bytes
    assert resolution_path.stat().st_ino == resolution_inode
    assert {key: path.read_bytes() for key, path in proposal_paths.items()} == (
        proposal_bytes
    )

    approved["reviewer"] = "Changed Reviewer"
    decision_path.write_text(
        yaml.safe_dump(approved, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(M1PreparationError, match="append-only resolution"):
        _record_m1_normalization_audit_internal(
            ROOT,
            packet_root=prepared.packet_root,
            manifest_root=prepared.manifest_root,
            decision_path=decision_path,
            inventory_path=inventory_path,
        )
    assert {key: path.read_bytes() for key, path in proposal_paths.items()} == (
        proposal_bytes
    )


def test_real_normalization_audit_decision_remains_pending_without_resolution() -> None:
    decision = yaml.safe_load(
        (ROOT / "reviews" / "M1-normalization-audit-decision.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert decision["decision"] == "pending"
    assert not (
        ROOT / "data" / "manifests" / HUMAN_AUDIT_RESOLUTION_NAME
    ).exists()


def test_record_normalization_audit_cli_fails_closed_while_decision_is_pending() -> None:
    resolution_path = (
        ROOT / "data" / "manifests" / HUMAN_AUDIT_RESOLUTION_NAME
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "judge_compare",
            "record-m1-normalization-audit",
            "--no-model-calls",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "not approved and valid" in f"{result.stdout}\n{result.stderr}"
    assert not resolution_path.exists()


def test_timing_preview_hash_tamper_fails_closed(tmp_path: Path) -> None:
    preview = tmp_path / "annotation-timing-example.md"
    preview.write_text("tampered preview\n", encoding="utf-8")
    record = {
        "preview_sha256": (
            "0168faf5d012a19448da74b8e13f25b937e76dfff5fb399854e7ba60494dba9a"
        )
    }

    with pytest.raises(M1PreparationError, match="preview hash does not match"):
        _validate_timing_preview_hash(preview, record)


def test_public_manifest_preflight_refuses_overwrite_drift(tmp_path: Path) -> None:
    paths = {
        key: tmp_path / filename for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    manifests = {
        key: {"schema_version": "1.0", "kind": key} for key in paths
    }
    paths["pool"].write_text('{"drifted": true}\n', encoding="utf-8")

    with pytest.raises(M1PreparationError, match="refusing to overwrite drifted"):
        _preflight_public_outputs(paths, manifests)


def test_cli_import_does_not_load_network_or_model_clients() -> None:
    command = (
        "import sys; import judge_compare.cli; "
        "blocked={'httpx','openai','typesafe_sdk','typesafe'}; "
        "loaded=blocked.intersection(sys.modules); "
        "assert not loaded, loaded"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
