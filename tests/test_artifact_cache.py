from datetime import UTC, datetime
from pathlib import Path

import pytest

from judge_compare.artifact_cache import materialize_artifact
from judge_compare.hashing import sha256_file


def test_existing_cache_requires_matching_prior_manifest(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("frozen", encoding="utf-8")

    with pytest.raises(ValueError, match="untracked cached artifact"):
        materialize_artifact(
            url="https://example.test/artifact.txt",
            destination=artifact,
            prior=None,
            retrieved_at=datetime.now(UTC),
        )


def test_existing_cache_is_verified_without_reblessing(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("frozen", encoding="utf-8")
    prior = {
        "url": "https://example.test/artifact.txt",
        "size_bytes": artifact.stat().st_size,
        "sha256": sha256_file(artifact),
        "etag": "etag",
        "last_modified": "yesterday",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
    }

    entry = materialize_artifact(
        url=prior["url"],
        destination=artifact,
        prior=prior,
        retrieved_at=datetime.now(UTC),
    )

    assert entry["sha256"] == prior["sha256"]
    assert entry["retrieved_at"] == prior["retrieved_at"]

    artifact.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="failed manifest verification"):
        materialize_artifact(
            url=prior["url"],
            destination=artifact,
            prior=prior,
            retrieved_at=datetime.now(UTC),
        )
