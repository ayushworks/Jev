from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from judge_compare.hashing import sha256_file
from judge_compare.net import download_atomic
from judge_compare.paths import portable_path


def load_prior_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected prior manifest object in {path}")
    return value


def materialize_artifact(
    *,
    url: str,
    destination: Path,
    prior: dict[str, Any] | None,
    retrieved_at: datetime,
    prior_retrieved_at: str | None = None,
) -> dict[str, str | int]:
    """Download a new artifact or verify an already recorded cache entry.

    Existing files are never silently re-blessed. They must have a prior manifest
    entry with the same URL, size, and digest.
    """
    if destination.exists():
        if prior is None:
            raise ValueError(
                f"refusing to trust untracked cached artifact: {portable_path(destination)}"
            )
        if prior.get("url") != url:
            raise ValueError(f"cached artifact URL changed for {portable_path(destination)}")
        digest = sha256_file(destination)
        size = destination.stat().st_size
        if prior.get("sha256") != digest or prior.get("size_bytes") != size:
            raise ValueError(
                f"cached artifact failed manifest verification: {portable_path(destination)}"
            )
        return {
            "url": url,
            "path": portable_path(destination),
            "size_bytes": size,
            "sha256": digest,
            "etag": str(prior.get("etag", "")),
            "last_modified": str(prior.get("last_modified", "")),
            "retrieved_at": str(
                prior.get("retrieved_at") or prior_retrieved_at or retrieved_at.isoformat()
            ),
        }

    downloaded = download_atomic(url, destination)
    return {
        "url": url,
        "path": portable_path(destination),
        "size_bytes": int(downloaded["size_bytes"]),
        "sha256": str(downloaded["sha256"]),
        "etag": str(downloaded.get("etag", "")),
        "last_modified": str(downloaded.get("last_modified", "")),
        "retrieved_at": retrieved_at.isoformat(),
    }
