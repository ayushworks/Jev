from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from judge_compare.hashing import canonical_sha256
from judge_compare.m1 import PUBLIC_MANIFEST_NAMES, prepare_m1
from judge_compare.normalization_audit import (
    AUDIT_CASE_COUNT,
    AUDIT_REVIEW_AID_NAME,
    AUDIT_WORKSHEET_NAME,
    AuditCandidate,
    NormalizationAuditPreparationError,
    _prepare_m1_normalization_audit_internal,
    _select_audit_candidates,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def prepared_private_audit(tmp_path_factory: pytest.TempPathFactory):
    m1 = prepare_m1(ROOT)
    public_paths = {
        key: m1.manifest_root / filename
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    public_before = {key: path.read_bytes() for key, path in public_paths.items()}
    audit_root = tmp_path_factory.mktemp("normalization-audit") / "audits"
    result = _prepare_m1_normalization_audit_internal(
        ROOT,
        m1=m1,
        audit_root=audit_root,
    )
    assert {key: path.read_bytes() for key, path in public_paths.items()} == public_before
    return m1, result


def test_private_audit_preparation_selects_exact_disjoint_covered_sample(
    prepared_private_audit,
) -> None:
    m1, result = prepared_private_audit
    inventory = result.inventory
    aid = result.review_aid

    assert set(inventory) == {
        "schema_version",
        "inventory_type",
        "private_packet_corpus_manifest_hash",
        "proposal_corpus_manifest_hash",
        "example_ids",
    }
    assert inventory["inventory_type"] == (
        "m1_normalization_audit_reviewed_examples"
    )
    assert len(inventory["example_ids"]) == len(set(inventory["example_ids"])) == 10
    assert aid["status"] == "prepared_for_human_review_not_reviewed_or_approved"
    assert aid["model_calls_made"] == 0
    assert aid["network_access_used"] is False
    assert aid["human_signoff_present"] is False
    assert len(aid["cases"]) == AUDIT_CASE_COUNT

    measured_families = {
        entry["task_family_id"]
        for entry in [
            *m1.pool_manifest["model_judgment"],
            *m1.pool_manifest["annotation_calibration"],
        ]
    }
    corpus_by_id = {
        entry["example_id"]: entry for entry in m1.corpus_manifest["entries"]
    }
    audit_families = {case["task_family_id"] for case in aid["cases"]}
    assert len(audit_families) == 10
    assert not (audit_families & measured_families)
    assert "retail-task-family-096" not in audit_families
    assert {
        corpus_by_id[example_id]["split"]
        for example_id in inventory["example_ids"]
    } == {"test"}

    assert [case["source_outcome"] for case in aid["cases"]].count("success") == 5
    assert [case["source_outcome"] for case in aid["cases"]].count("failure") == 5
    assert {
        (case["size_band"], case["source_outcome"])
        for case in aid["cases"]
    }.issuperset(
        {
            ("short", "success"),
            ("short", "failure"),
            ("long", "success"),
            ("long", "failure"),
        }
    )
    assert [case["example_id"] for case in aid["cases"]] == inventory["example_ids"]
    for case in aid["cases"]:
        assert canonical_sha256(case["original_source_simulation"]) == (
            case["source_simulation_hash"]
        )
        assert canonical_sha256(case["canonical_packet"]) == (
            case["canonical_packet_hash"]
        )
        assert set(case["review"]["checks"]) == set(aid["required_checks"])
        assert set(case["review"]["checks"].values()) == {None}
        assert case["review"]["notes"] is None


def test_private_audit_outputs_are_unchecked_and_idempotent(
    prepared_private_audit,
) -> None:
    m1, result = prepared_private_audit
    paths = (result.inventory_path, result.review_aid_path, result.worksheet_path)
    before_bytes = {path: path.read_bytes() for path in paths}
    before_inodes = {path: path.stat().st_ino for path in paths}

    repeated = _prepare_m1_normalization_audit_internal(
        ROOT,
        m1=m1,
        audit_root=result.inventory_path.parent,
    )

    assert repeated.inventory == result.inventory
    assert repeated.review_aid == result.review_aid
    assert {path: path.read_bytes() for path in paths} == before_bytes
    assert {path: path.stat().st_ino for path in paths} == before_inodes
    worksheet = result.worksheet_path.read_text(encoding="utf-8")
    assert "PREPARED ONLY — NOT REVIEWED OR APPROVED" in worksheet
    assert worksheet.count("- [ ]") == (10 * 6) + 4
    assert result.review_aid_path.name == AUDIT_REVIEW_AID_NAME
    assert result.worksheet_path.name == AUDIT_WORKSHEET_NAME


def test_private_audit_preflight_refuses_drift_without_partial_writes(
    tmp_path: Path,
) -> None:
    m1 = prepare_m1(ROOT)
    audit_root = tmp_path / "audits"
    result = _prepare_m1_normalization_audit_internal(
        ROOT,
        m1=m1,
        audit_root=audit_root,
    )
    inventory_before = result.inventory_path.read_bytes()
    aid_before = result.review_aid_path.read_bytes()
    result.worksheet_path.write_text("human-edited worksheet\n", encoding="utf-8")

    with pytest.raises(
        NormalizationAuditPreparationError,
        match="refusing to overwrite drifted private audit artifact",
    ):
        _prepare_m1_normalization_audit_internal(
            ROOT,
            m1=m1,
            audit_root=audit_root,
        )

    assert result.inventory_path.read_bytes() == inventory_before
    assert result.review_aid_path.read_bytes() == aid_before
    assert result.worksheet_path.read_text(encoding="utf-8") == (
        "human-edited worksheet\n"
    )


def _candidate(
    number: int,
    *,
    band: str,
    outcome: str,
) -> AuditCandidate:
    return AuditCandidate(
        example_id=f"example-{number:02d}",
        task_family_id=f"family-{number:02d}",
        task_id=str(number),
        trial=0,
        packet_size_bytes={"short": 100, "middle": 200, "long": 300}[band],
        size_band=band,  # type: ignore[arg-type]
        source_outcome=outcome,  # type: ignore[arg-type]
        source_reward=1.0 if outcome == "success" else 0.0,
        source_simulation_id=f"source-{number:02d}",
        source_simulation_index=number,
        source_simulation={},
        packet={},
        packet_path=f"packets/example-{number:02d}.json",
        packet_hash="a" * 64,
        source_simulation_hash="b" * 64,
    )


def test_audit_selection_is_order_independent_and_deterministic() -> None:
    candidates = [
        _candidate(0, band="short", outcome="success"),
        _candidate(1, band="short", outcome="failure"),
        _candidate(2, band="long", outcome="success"),
        _candidate(3, band="long", outcome="failure"),
        _candidate(4, band="middle", outcome="success"),
        _candidate(5, band="middle", outcome="success"),
        _candidate(6, band="middle", outcome="success"),
        _candidate(7, band="middle", outcome="failure"),
        _candidate(8, band="middle", outcome="failure"),
        _candidate(9, band="middle", outcome="failure"),
        _candidate(10, band="middle", outcome="success"),
        _candidate(11, band="middle", outcome="failure"),
    ]
    forward = _select_audit_candidates(candidates, seed=20260921)
    reverse = _select_audit_candidates(list(reversed(candidates)), seed=20260921)
    assert [case.example_id for case in forward] == [
        case.example_id for case in reverse
    ]
    assert len({case.task_family_id for case in forward}) == 10


def test_normalization_audit_import_has_no_network_or_model_clients() -> None:
    command = (
        "import sys; import judge_compare.normalization_audit; "
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


def test_review_aid_is_private_and_decision_remains_pending(
    prepared_private_audit,
) -> None:
    _, result = prepared_private_audit
    decision = (ROOT / "reviews" / "M1-normalization-audit-decision.yaml").read_text(
        encoding="utf-8"
    )
    assert "decision: pending" in decision
    assert not (
        ROOT / "data" / "manifests" / "m1-normalization-audit-resolution.json"
    ).exists()
    serialized = json.loads(result.review_aid_path.read_text(encoding="utf-8"))
    assert serialized["privacy"] == (
        "private_gitignored_contains_source_content_rewards_and_ids"
    )
