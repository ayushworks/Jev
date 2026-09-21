from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from judge_compare.config import load_config
from judge_compare.hashing import sha256_file
from judge_compare.io import write_json_atomic
from judge_compare.paths import portable_path, safe_join

G0_DOCUMENTS = (
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "configs/experiment.yaml",
    "configs/g0-gpt54.yaml",
    "data/manifests/upstream_archive_manifest.json",
    "data/manifests/candidate_source_manifest.json",
    "data/manifests/candidate_source_sample.json",
    "reports/upstream-audit.md",
    "reports/candidate-source-audit.md",
    "protocol/upstream-reuse.md",
    "protocol/protocol-v2.md",
    "protocol/source-selection.md",
    "protocol/model-options.md",
    "protocol/rubric-draft.md",
    "protocol/analysis-plan.md",
    "protocol/budget.md",
    "reviews/G0-request.md",
)


def _git_revision(root: Path) -> dict[str, Any]:
    head_result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if status_result.returncode != 0:
        raise ValueError("G0 packaging requires an initialized Git repository")
    head = head_result.stdout.strip() if head_result.returncode == 0 else None
    changed_paths = [line for line in status_result.stdout.splitlines() if line]
    return {
        "git_head": head,
        "state": "clean" if not changed_paths else "uncommitted_or_dirty",
        "changed_path_count": len(changed_paths),
        "freeze_basis": "per-file SHA-256 entries in this manifest",
    }


def _validated_audit_status(root: Path, path: Path) -> None:
    import json

    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    status = manifest.get("findings", {}).get("audit_status")
    if status != "validated":
        raise ValueError(f"audit is not validated: {portable_path(path)} ({status!r})")
    artifacts = manifest.get("artifacts")
    entries = list(artifacts.values()) if isinstance(artifacts, dict) else artifacts
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"audit has no artifact inventory: {portable_path(path)}")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError(f"malformed artifact entry in {portable_path(path)}")
        recorded_path = Path(entry["path"])
        if recorded_path.is_absolute():
            raise ValueError(f"audit artifact path must be portable: {recorded_path}")
        artifact_path = safe_join(root, *recorded_path.parts)
        if (
            not artifact_path.is_file()
            or artifact_path.stat().st_size != entry.get("size_bytes")
            or sha256_file(artifact_path) != entry.get("sha256")
        ):
            raise ValueError(f"audit artifact failed package verification: {recorded_path}")


def prepare_g0_manifest(
    root: Path,
    output_path: Path,
    *,
    prepared_at: datetime | None = None,
) -> dict[str, Any]:
    """Freeze the review inputs without selecting a proposal or calling a model."""
    decision_path = safe_join(root, "reviews", "G0-decision.yaml")
    if decision_path.is_file():
        with decision_path.open("r", encoding="utf-8") as handle:
            decision = yaml.safe_load(handle)
        if isinstance(decision, dict) and decision.get("decision") == "approved":
            raise ValueError(
                "G0 is already approved; preserve the reviewed manifest instead of "
                "regenerating it"
            )

    active = load_config(safe_join(root, "configs", "experiment.yaml"))
    if not active.pending_review_paths():
        raise ValueError(
            "G0 is already resolved; preserve the reviewed manifest instead of "
            "regenerating it"
        )
    for name in ("g0-gpt54.yaml",):
        proposal = load_config(safe_join(root, "configs", name))
        proposal.assert_g0_resolved()

    _validated_audit_status(
        root, safe_join(root, "data", "manifests", "upstream_archive_manifest.json")
    )
    _validated_audit_status(
        root, safe_join(root, "data", "manifests", "candidate_source_manifest.json")
    )

    relative_paths = list(G0_DOCUMENTS)
    relative_paths.extend(
        portable_path(path)
        for directory in (safe_join(root, "src"), safe_join(root, "tests"))
        for path in directory.rglob("*.py")
    )
    entries = []
    for relative in sorted(set(relative_paths)):
        path = safe_join(root, *relative.split("/"))
        if not path.is_file():
            raise ValueError(f"missing G0 artifact: {relative}")
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    manifest = {
        "schema_version": "1.0",
        "gate": "G0",
        "status": "pending_owner_decision",
        "prepared_at": (prepared_at or datetime.now(UTC)).isoformat(),
        "model_calls_made": 0,
        "spend_authorized": False,
        "code_revision": _git_revision(root),
        "artifacts": entries,
    }
    write_json_atomic(output_path, manifest)
    return manifest
