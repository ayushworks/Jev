from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from judge_compare.hashing import sha256_file

DEFAULT_TIMEOUT = httpx.Timeout(connect=15, read=120, write=30, pool=15)


def download_atomic(
    url: str,
    destination: Path,
    *,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    expected_sha256: str | None = None,
    progress: Callable[[int], None] | None = None,
) -> dict[str, str | int]:
    """Download *url* without exposing a partially written destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            response.raise_for_status()
            total = 0
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    total += len(chunk)
                    if progress:
                        progress(total)
                handle.flush()
                os.fsync(handle.fileno())
            etag = response.headers.get("etag", "").strip('"')
            last_modified = response.headers.get("last-modified", "")
        digest = sha256_file(temporary)
        if expected_sha256 and digest != expected_sha256:
            raise ValueError(
                f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {digest}"
            )
        os.replace(temporary, destination)
        return {
            "url": url,
            "path": str(destination),
            "size_bytes": total,
            "sha256": digest,
            "etag": etag,
            "last_modified": last_modified,
        }
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
