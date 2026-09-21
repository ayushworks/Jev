from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Return the repository root from an installed or source checkout."""
    return Path(__file__).resolve().parents[2]


def safe_join(root: Path, *parts: str) -> Path:
    """Join untrusted path components without allowing a root escape."""
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"path escapes configured root: {candidate}")
    return candidate


def portable_path(path: Path) -> str:
    """Prefer a repository-relative manifest path when possible."""
    resolved = path.resolve()
    root = project_root().resolve()
    if resolved.is_relative_to(root):
        return resolved.relative_to(root).as_posix()
    return str(resolved)
