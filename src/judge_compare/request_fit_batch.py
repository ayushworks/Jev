from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from judge_compare.config import load_config
from judge_compare.hashing import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_bytes,
    sha256_file,
)
from judge_compare.m1 import PUBLIC_MANIFEST_NAMES, M1Preparation, prepare_m1
from judge_compare.models import EvidencePacket
from judge_compare.paths import safe_join
from judge_compare.request_fit import (
    REQUEST_ENVELOPE_SCHEMA_VERSION,
    JudgeRequestEnvelopes,
    RequestFitError,
    VersionedRubric,
    build_judge_request_envelopes,
)

REQUEST_FIT_BATCH_SCHEMA_VERSION = "1.0"
REQUEST_FIT_RUBRIC_VERSION = "rubric-draft-1"
EXPECTED_CANONICAL_PACKETS = 456
EXPECTED_EXPOSURE_EXCLUSIONS = 8
EXPECTED_REQUEST_FIT_ENVELOPES = 448
REQUEST_FIT_ROOT_RELATIVE = ("data", "packets", "v1", "request-fit")
REQUEST_FIT_MANIFEST_NAME = "manifest.json"
REQUEST_FIT_ENVELOPE_DIR_NAME = "envelopes"
_ELIGIBLE_STATUS = "provisional_pending_exact_context_eligibility"
_EXCLUDED_STATUS = "excluded_owner_exposure"
_EXAMPLE_ID_PATTERN = re.compile(r"^cs-[0-9a-f]{24}$")


class M1RequestFitPreparationError(RequestFitError):
    """Raised when the bounded offline request-fit batch cannot be preserved safely."""


@dataclass(frozen=True, slots=True)
class M1RequestFitPreparation:
    manifest: dict[str, Any]
    output_root: Path
    manifest_path: Path
    envelope_dir: Path
    envelope_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _PreparedEnvelopeFile:
    example_id: str
    filename: str
    payload: bytes
    envelope: JudgeRequestEnvelopes
    public_entry: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    payload: bytes
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class _LockedOutputDirectories:
    root_fd: int
    envelope_fd: int
    root_identity: tuple[int, int]
    envelope_identity: tuple[int, int]


def prepare_m1_request_fit(root: Path) -> M1RequestFitPreparation:
    """Prepare all M1 request envelopes without provider or token-count calls.

    The public entry point is intentionally fixed to the approved private output
    root. It first reruns the existing M1 preparation validation and proves that
    the four public proposal manifests were not touched.
    """

    resolved_root = root.resolve()
    output_root = resolved_root.joinpath(*REQUEST_FIT_ROOT_RELATIVE)
    _assert_private_request_fit_root(resolved_root, output_root)
    before = _snapshot_public_m1_manifests(resolved_root)
    m1 = prepare_m1(resolved_root)
    after = _snapshot_public_m1_manifests(resolved_root)
    if before != after:
        raise M1RequestFitPreparationError(
            "prepare_m1 changed a public M1 proposal manifest; refusing request-fit output"
        )
    result = _prepare_m1_request_fit_internal(
        resolved_root,
        m1=m1,
        output_root=output_root,
        require_canonical_paths=True,
        expected_public_snapshot=before,
    )
    final = _snapshot_public_m1_manifests(resolved_root)
    if before != final:
        raise M1RequestFitPreparationError(
            "a public M1 proposal manifest changed during request-fit preparation"
        )
    return result


def _prepare_m1_request_fit_internal(
    root: Path,
    *,
    m1: M1Preparation,
    output_root: Path,
    require_canonical_paths: bool = False,
    expected_public_snapshot: Mapping[str, _FileSnapshot] | None = None,
) -> M1RequestFitPreparation:
    """Build and write a deterministic private batch after complete preflight."""

    resolved_root = root.resolve()
    resolved_output_root = output_root.resolve()
    if require_canonical_paths:
        expected_output_root = safe_join(resolved_root, *REQUEST_FIT_ROOT_RELATIVE)
        if resolved_output_root != expected_output_root:
            raise M1RequestFitPreparationError(
                "public request-fit preparation may write only under "
                "data/packets/v1/request-fit"
            )
        if m1.packet_root.resolve() != safe_join(resolved_root, "data", "packets", "v1"):
            raise M1RequestFitPreparationError(
                "prepare_m1 returned a noncanonical private packet root"
            )
        if m1.manifest_root.resolve() != safe_join(resolved_root, "data", "manifests"):
            raise M1RequestFitPreparationError(
                "prepare_m1 returned a noncanonical public manifest root"
            )

    config_path = safe_join(resolved_root, "configs", "experiment.yaml")
    rubric_path = safe_join(resolved_root, "protocol", "rubric-draft.md")
    config = load_config(config_path)
    rubric = VersionedRubric.from_path(
        rubric_path,
        version=REQUEST_FIT_RUBRIC_VERSION,
    )
    eligible_entries, m1_bindings = _validate_m1_preparation(
        resolved_root,
        m1=m1,
        config_path=config_path,
    )

    prepared: list[_PreparedEnvelopeFile] = []
    for public_entry in eligible_entries:
        example_id = str(public_entry["example_id"])
        packet = _load_bound_packet(
            resolved_root,
            m1=m1,
            public_entry=public_entry,
        )
        envelope = build_judge_request_envelopes(packet, rubric, config)
        if envelope.example_id != example_id:
            raise M1RequestFitPreparationError(
                f"request envelope example_id mismatch: {example_id}"
            )
        payload = _pretty_json_bytes(envelope.model_dump(mode="json"))
        # Validate the exact on-disk representation before it can enter preflight.
        decoded = _decode_json_object(payload, context=f"request envelope {example_id}")
        if JudgeRequestEnvelopes.model_validate(decoded) != envelope:
            raise M1RequestFitPreparationError(
                f"request envelope round-trip mismatch: {example_id}"
            )
        prepared.append(
            _PreparedEnvelopeFile(
                example_id=example_id,
                filename=f"{example_id}.json",
                payload=payload,
                envelope=envelope,
                public_entry=dict(public_entry),
            )
        )

    if len(prepared) != EXPECTED_REQUEST_FIT_ENVELOPES:
        raise M1RequestFitPreparationError(
            "request-fit batch must contain exactly 448 structurally eligible packets"
        )
    if len({item.example_id for item in prepared}) != len(prepared):
        raise M1RequestFitPreparationError("request-fit batch contains duplicate example IDs")
    if len({item.envelope.packet_hash for item in prepared}) != len(prepared):
        raise M1RequestFitPreparationError("request-fit batch contains duplicate packet hashes")

    entries = [
        {
            "preparation_order": index,
            "example_id": item.example_id,
            "task_id": str(item.public_entry["task_id"]),
            "trial": int(item.public_entry["trial"]),
            "task_family_id": str(item.public_entry["task_family_id"]),
            "split": str(item.public_entry["split"]),
            "packet_hash": item.envelope.packet_hash,
            "rubric_hash": item.envelope.rubric_hash,
            "config_hash": item.envelope.config_hash,
            "envelope_hash": item.envelope.envelope_hash,
            "gpt_response_create_body_hash": (item.envelope.gpt_response_create_body_hash),
            "gpt_input_token_count_body_hash": (item.envelope.gpt_input_token_count_body_hash),
            "jev_system_one_body_hash": item.envelope.jev_system_one_body_hash,
            "envelope_path": (f"{REQUEST_FIT_ENVELOPE_DIR_NAME}/{item.filename}"),
            "envelope_file_sha256": sha256_bytes(item.payload),
            "envelope_file_size_bytes": len(item.payload),
            "exact_token_count_status": "not_executed",
        }
        for index, item in enumerate(prepared, start=1)
    ]
    config_hashes = {entry["config_hash"] for entry in entries}
    rubric_hashes = {entry["rubric_hash"] for entry in entries}
    if len(config_hashes) != 1 or len(rubric_hashes) != 1:
        raise M1RequestFitPreparationError(
            "all request envelopes must share one approved config and rubric"
        )

    manifest_unsealed: dict[str, Any] = {
        "schema_version": REQUEST_FIT_BATCH_SCHEMA_VERSION,
        "artifact_type": "m1_offline_request_fit_envelopes",
        "status": "canonical_envelopes_prepared_exact_counts_pending",
        "privacy": "private_gitignored",
        "evaluation_unit": "final_customer_facing_response",
        "request_envelope_schema_version": REQUEST_ENVELOPE_SCHEMA_VERSION,
        "network_access_used": False,
        "provider_sdk_calls_made": 0,
        "model_calls_made": 0,
        "token_count_calls_made": 0,
        "exact_token_counts_recorded": 0,
        "truncation_used": False,
        "bindings": {
            **m1_bindings,
            "config_path": "configs/experiment.yaml",
            "config_source_sha256": sha256_file(config_path),
            "config_hash": next(iter(config_hashes)),
            "rubric_path": "protocol/rubric-draft.md",
            "rubric_version": rubric.version,
            "rubric_hash": rubric.sha256,
            "request_fit_builder_sha256": sha256_file(
                safe_join(resolved_root, "src", "judge_compare", "request_fit.py")
            ),
            "batch_preparation_sha256": sha256_file(Path(__file__)),
        },
        "counts": {
            "canonical_packets": EXPECTED_CANONICAL_PACKETS,
            "owner_exposure_exclusions": EXPECTED_EXPOSURE_EXCLUSIONS,
            "structurally_eligible_non_exposed": len(prepared),
            "request_envelopes": len(entries),
            "gpt_exact_count_records": 0,
            "jev_exact_count_records": 0,
        },
        "limits": {
            "maximum_envelopes": EXPECTED_REQUEST_FIT_ENVELOPES,
            "one_envelope_per_packet": True,
        },
        "entries": entries,
    }
    manifest = {
        **manifest_unsealed,
        "manifest_hash": canonical_sha256(manifest_unsealed),
    }
    manifest_payload = _pretty_json_bytes(manifest)

    envelope_dir = safe_join(
        resolved_output_root,
        REQUEST_FIT_ENVELOPE_DIR_NAME,
    )
    manifest_path = safe_join(
        resolved_output_root,
        REQUEST_FIT_MANIFEST_NAME,
    )
    expected_files = {safe_join(envelope_dir, item.filename): item.payload for item in prepared}
    expected_files[manifest_path] = manifest_payload
    if expected_public_snapshot is not None and dict(expected_public_snapshot) != (
        _snapshot_public_m1_manifests(resolved_root)
    ):
        raise M1RequestFitPreparationError(
            "a public M1 proposal manifest changed before the request-fit write phase"
        )
    if require_canonical_paths:
        _assert_artifacts_are_gitignored(resolved_root, tuple(expected_files))
    _preflight_request_fit_output(
        resolved_output_root,
        envelope_dir=envelope_dir,
        manifest_path=manifest_path,
        expected_files=expected_files,
    )

    with _locked_output_directories(
        resolved_output_root,
        envelope_dir,
    ) as locked:
        _assert_locked_directory_identities(
            locked,
            output_root=resolved_output_root,
            envelope_dir=envelope_dir,
        )
        # Repeat preflight while holding the cooperative directory lock.
        _preflight_request_fit_output(
            resolved_output_root,
            envelope_dir=envelope_dir,
            manifest_path=manifest_path,
            expected_files=expected_files,
        )
        for item in prepared:
            _write_new_exact_at(
                locked.envelope_fd,
                item.filename,
                item.payload,
            )
        # The sealed manifest is the completion marker and is deliberately written last.
        _write_new_exact_at(
            locked.root_fd,
            REQUEST_FIT_MANIFEST_NAME,
            manifest_payload,
        )
        os.fsync(locked.envelope_fd)
        os.fsync(locked.root_fd)
        _assert_locked_directory_identities(
            locked,
            output_root=resolved_output_root,
            envelope_dir=envelope_dir,
        )
    _preflight_request_fit_output(
        resolved_output_root,
        envelope_dir=envelope_dir,
        manifest_path=manifest_path,
        expected_files=expected_files,
        require_complete=True,
    )
    if require_canonical_paths:
        _assert_artifacts_are_gitignored(resolved_root, tuple(expected_files))

    return M1RequestFitPreparation(
        manifest=manifest,
        output_root=resolved_output_root,
        manifest_path=manifest_path,
        envelope_dir=envelope_dir,
        envelope_paths=tuple(safe_join(envelope_dir, item.filename) for item in prepared),
    )


def _validate_m1_preparation(
    root: Path,
    *,
    m1: M1Preparation,
    config_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    manifests = {
        "corpus": m1.corpus_manifest,
        "split": m1.split_manifest,
        "pool": m1.pool_manifest,
        "exclusion": m1.exclusion_manifest,
    }
    for name, manifest in manifests.items():
        _validate_sealed_manifest(manifest, context=f"M1 {name} proposal")

    corpus = m1.corpus_manifest
    counts = corpus.get("counts")
    entries = corpus.get("entries")
    if not isinstance(counts, dict) or not isinstance(entries, list):
        raise M1RequestFitPreparationError("M1 corpus proposal is malformed")
    if (
        counts.get("canonical_packets") != EXPECTED_CANONICAL_PACKETS
        or counts.get("owner_exposure_exclusions") != EXPECTED_EXPOSURE_EXCLUSIONS
        or counts.get("structurally_eligible_after_exposure_exclusion")
        != EXPECTED_REQUEST_FIT_ENVELOPES
        or len(entries) != EXPECTED_CANONICAL_PACKETS
    ):
        raise M1RequestFitPreparationError("M1 corpus counts do not match the approved frame")

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            raise M1RequestFitPreparationError("M1 corpus contains a malformed entry")
        example_id = raw_entry.get("example_id")
        if not isinstance(example_id, str) or not _EXAMPLE_ID_PATTERN.fullmatch(example_id):
            raise M1RequestFitPreparationError("M1 corpus contains an unsafe example ID")
        if example_id in seen_ids:
            raise M1RequestFitPreparationError("M1 corpus contains duplicate example IDs")
        seen_ids.add(example_id)
        status = raw_entry.get("pool_eligibility")
        if status == _ELIGIBLE_STATUS:
            eligible.append(dict(raw_entry))
        elif status == _EXCLUDED_STATUS:
            excluded.append(dict(raw_entry))
        else:
            raise M1RequestFitPreparationError(
                f"M1 corpus has unsupported pool eligibility: {example_id}"
            )
    if len(eligible) != EXPECTED_REQUEST_FIT_ENVELOPES:
        raise M1RequestFitPreparationError("M1 must expose exactly 448 eligible packets")
    if len(excluded) != EXPECTED_EXPOSURE_EXCLUSIONS:
        raise M1RequestFitPreparationError("M1 must expose exactly eight excluded packets")

    exclusion_entries = m1.exclusion_manifest.get("entries")
    if not isinstance(exclusion_entries, list):
        raise M1RequestFitPreparationError("M1 exclusion proposal is malformed")
    excluded_ids = {str(entry["example_id"]) for entry in excluded}
    declared_excluded_ids = {
        str(entry.get("example_id")) for entry in exclusion_entries if isinstance(entry, dict)
    }
    if excluded_ids != declared_excluded_ids:
        raise M1RequestFitPreparationError(
            "M1 corpus and exclusion proposal do not identify the same packets"
        )

    bindings = corpus.get("bindings")
    if not isinstance(bindings, dict):
        raise M1RequestFitPreparationError("M1 corpus proposal has no binding block")
    private_manifest_path = safe_join(m1.packet_root.resolve(), "corpus_manifest.json")
    private_manifest = _decode_json_object(
        _read_regular_file(private_manifest_path, context="private M1 corpus manifest"),
        context="private M1 corpus manifest",
    )
    _validate_sealed_manifest(private_manifest, context="private M1 corpus manifest")
    private_manifest_hash = private_manifest.get("manifest_hash")
    if private_manifest_hash != bindings.get("private_packet_corpus_manifest_hash"):
        raise M1RequestFitPreparationError(
            "private M1 corpus manifest does not match the public proposal"
        )
    private_entries = private_manifest.get("entries")
    if (
        private_manifest.get("included_packet_count") != EXPECTED_CANONICAL_PACKETS
        or not isinstance(private_entries, list)
        or len(private_entries) != EXPECTED_CANONICAL_PACKETS
    ):
        raise M1RequestFitPreparationError("private M1 corpus frame is malformed")
    private_by_id: dict[str, Mapping[str, Any]] = {}
    for entry in private_entries:
        if not isinstance(entry, dict):
            raise M1RequestFitPreparationError("private M1 corpus contains a malformed entry")
        example_id = entry.get("example_id")
        if not isinstance(example_id, str) or example_id in private_by_id:
            raise M1RequestFitPreparationError(
                "private M1 corpus contains duplicate or malformed example IDs"
            )
        private_by_id[example_id] = entry
    if set(private_by_id) != seen_ids:
        raise M1RequestFitPreparationError(
            "public and private M1 corpus example IDs do not match"
        )
    for public_entry in entries:
        example_id = str(public_entry["example_id"])
        private_entry = private_by_id[example_id]
        if any(
            private_entry.get(field) != public_entry.get(field)
            for field in ("task_id", "trial", "packet_hash", "packet_size_bytes")
        ):
            raise M1RequestFitPreparationError(
                f"public/private M1 packet binding mismatch: {example_id}"
            )
    config_source_hash = sha256_file(config_path)
    if config_source_hash != bindings.get("active_config_sha256"):
        raise M1RequestFitPreparationError(
            "active config no longer matches the M1 approved binding"
        )

    eligible.sort(
        key=lambda entry: (
            int(entry["task_id"]),
            int(entry["trial"]),
            str(entry["example_id"]),
        )
    )
    return eligible, {
        "m1_corpus_manifest_hash": str(corpus["manifest_hash"]),
        "m1_split_manifest_hash": str(m1.split_manifest["manifest_hash"]),
        "m1_pool_manifest_hash": str(m1.pool_manifest["manifest_hash"]),
        "m1_exclusion_manifest_hash": str(m1.exclusion_manifest["manifest_hash"]),
        "m1_private_corpus_manifest_hash": str(private_manifest_hash),
        "g0_decision_sha256": str(bindings["g0_decision_sha256"]),
        "g0_package_sha256": str(bindings["g0_package_sha256"]),
        "active_config_sha256": str(bindings["active_config_sha256"]),
    }


def _load_bound_packet(
    root: Path,
    *,
    m1: M1Preparation,
    public_entry: Mapping[str, Any],
) -> EvidencePacket:
    example_id = str(public_entry["example_id"])
    packet_path = safe_join(m1.packet_root.resolve(), "packets", f"{example_id}.json")
    declared = public_entry.get("packet_path")
    if not isinstance(declared, str):
        raise M1RequestFitPreparationError(f"M1 packet path is missing: {example_id}")
    declared_path = Path(declared)
    if not declared_path.is_absolute():
        declared_path = root / declared_path
    if declared_path.resolve() != packet_path:
        raise M1RequestFitPreparationError(
            f"M1 packet path does not match its private packet: {example_id}"
        )
    raw = _decode_json_object(
        _read_regular_file(packet_path, context=f"M1 packet {example_id}"),
        context=f"M1 packet {example_id}",
    )
    packet = EvidencePacket.model_validate(raw)
    canonical_packet = packet.model_dump(mode="json")
    if raw != canonical_packet:
        raise M1RequestFitPreparationError(f"M1 packet is not canonical: {example_id}")
    packet_hash = canonical_sha256(canonical_packet)
    if packet_hash != public_entry.get("packet_hash"):
        raise M1RequestFitPreparationError(f"M1 packet hash mismatch: {example_id}")
    if len(canonical_json_bytes(canonical_packet)) != public_entry.get("packet_size_bytes"):
        raise M1RequestFitPreparationError(f"M1 packet size mismatch: {example_id}")
    return packet


def _validate_sealed_manifest(manifest: Mapping[str, Any], *, context: str) -> None:
    recorded = manifest.get("manifest_hash")
    if not isinstance(recorded, str):
        raise M1RequestFitPreparationError(f"{context} has no manifest hash")
    unsealed = dict(manifest)
    unsealed.pop("manifest_hash", None)
    if canonical_sha256(unsealed) != recorded:
        raise M1RequestFitPreparationError(f"{context} hash does not match its content")


def _assert_private_request_fit_root(root: Path, output_root: Path) -> None:
    expected_lexical = root.resolve().joinpath(*REQUEST_FIT_ROOT_RELATIVE)
    if output_root.absolute() != expected_lexical.absolute():
        raise M1RequestFitPreparationError(
            "request-fit artifacts may be written only under data/packets/v1/request-fit"
        )
    current = root.resolve()
    for part in REQUEST_FIT_ROOT_RELATIVE:
        current = current / part
        if current.is_symlink():
            raise M1RequestFitPreparationError(
                "request-fit output path cannot contain symlink components"
            )
    try:
        expected = safe_join(root, *REQUEST_FIT_ROOT_RELATIVE)
    except ValueError as exc:
        raise M1RequestFitPreparationError(
            "request-fit output path escapes the repository"
        ) from exc
    if output_root.resolve() != expected:
        raise M1RequestFitPreparationError(
            "request-fit artifacts may be written only under data/packets/v1/request-fit"
        )
    probes = (
        Path(*REQUEST_FIT_ROOT_RELATIVE, REQUEST_FIT_MANIFEST_NAME).as_posix(),
        Path(
            *REQUEST_FIT_ROOT_RELATIVE,
            REQUEST_FIT_ENVELOPE_DIR_NAME,
            ".request-fit-ignore-probe.json",
        ).as_posix(),
    )
    for probe in probes:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", probe],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise M1RequestFitPreparationError(
                "request-fit manifest and envelope files must be covered by git ignore rules"
            )


def _assert_artifacts_are_gitignored(root: Path, paths: tuple[Path, ...]) -> None:
    relative_paths: list[str] = []
    for path in paths:
        try:
            relative = path.absolute().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise M1RequestFitPreparationError(
                "request-fit artifact path is outside the repository"
            ) from exc
        relative_paths.append(relative)
    if len(relative_paths) != len(set(relative_paths)):
        raise M1RequestFitPreparationError("request-fit artifact paths are not unique")

    encoded = b"\0".join(path.encode("utf-8") for path in relative_paths) + b"\0"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        input=encoded,
    )
    if result.returncode not in {0, 1}:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise M1RequestFitPreparationError(
            f"could not verify request-fit git-ignore coverage: {detail}"
        )
    ignored = {value.decode("utf-8") for value in result.stdout.split(b"\0") if value}
    missing = sorted(set(relative_paths) - ignored)
    if missing:
        preview = missing[:3]
        suffix = "" if len(missing) <= 3 else f" (+{len(missing) - 3} more)"
        raise M1RequestFitPreparationError(
            f"request-fit artifacts are not all covered by git ignore rules: {preview}{suffix}"
        )
    tracked_result = subprocess.run(
        ["git", "ls-files", "--cached", "-z", "--", *relative_paths],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if tracked_result.returncode != 0:
        detail = tracked_result.stderr.decode("utf-8", errors="replace").strip()
        raise M1RequestFitPreparationError(
            f"could not verify request-fit tracked-file status: {detail}"
        )
    tracked = sorted(
        value.decode("utf-8") for value in tracked_result.stdout.split(b"\0") if value
    )
    if tracked:
        preview = tracked[:3]
        suffix = "" if len(tracked) <= 3 else f" (+{len(tracked) - 3} more)"
        raise M1RequestFitPreparationError(
            f"request-fit artifacts must not be tracked by git: {preview}{suffix}"
        )


def _snapshot_public_m1_manifests(root: Path) -> dict[str, _FileSnapshot]:
    snapshots: dict[str, _FileSnapshot] = {}
    manifest_root = safe_join(root, "data", "manifests")
    for key, filename in PUBLIC_MANIFEST_NAMES.items():
        path = safe_join(manifest_root, filename)
        payload = _read_regular_file(path, context=f"public M1 {key} proposal")
        stat = path.stat()
        snapshots[key] = _FileSnapshot(
            payload=payload,
            inode=stat.st_ino,
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
        )
    return snapshots


def _preflight_request_fit_output(
    output_root: Path,
    *,
    envelope_dir: Path,
    manifest_path: Path,
    expected_files: Mapping[Path, bytes],
    require_complete: bool = False,
) -> None:
    if output_root.is_symlink():
        raise M1RequestFitPreparationError("request-fit output root cannot be a symlink")
    if output_root.exists() and not output_root.is_dir():
        raise M1RequestFitPreparationError("request-fit output root must be a directory")
    if not output_root.exists():
        if require_complete:
            raise M1RequestFitPreparationError("request-fit output root is incomplete")
        return

    allowed_root_names = {REQUEST_FIT_ENVELOPE_DIR_NAME, REQUEST_FIT_MANIFEST_NAME}
    unexpected_root = {path.name for path in output_root.iterdir()} - allowed_root_names
    if unexpected_root:
        raise M1RequestFitPreparationError(
            f"request-fit output contains unexpected entries: {sorted(unexpected_root)}"
        )
    if envelope_dir.is_symlink():
        raise M1RequestFitPreparationError("request-fit envelope directory cannot be a symlink")
    if envelope_dir.exists() and not envelope_dir.is_dir():
        raise M1RequestFitPreparationError("request-fit envelope path must be a directory")

    expected_envelope_names = {
        path.name for path in expected_files if path.parent == envelope_dir
    }
    if envelope_dir.exists():
        observed = list(envelope_dir.iterdir())
        unexpected = {path.name for path in observed} - expected_envelope_names
        if unexpected:
            raise M1RequestFitPreparationError(
                f"request-fit envelope directory contains unexpected files: {sorted(unexpected)}"
            )
        if any(path.is_symlink() or not path.is_file() for path in observed):
            raise M1RequestFitPreparationError(
                "request-fit envelope directory must contain only regular files"
            )

    for path, expected in expected_files.items():
        if path.exists() or path.is_symlink():
            observed = _read_regular_file(path, context=f"request-fit artifact {path.name}")
            if observed != expected:
                raise M1RequestFitPreparationError(
                    f"refusing to overwrite drifted request-fit artifact: {path.name}"
                )
        elif require_complete:
            raise M1RequestFitPreparationError(
                f"request-fit output is missing expected artifact: {path.name}"
            )
    if manifest_path.exists() and manifest_path.is_symlink():
        raise M1RequestFitPreparationError("request-fit manifest cannot be a symlink")


@contextmanager
def _locked_output_directories(
    output_root: Path,
    envelope_dir: Path,
):
    output_root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd: int | None = None
    envelope_fd: int | None = None
    try:
        root_fd = os.open(output_root, flags)
        fcntl.flock(root_fd, fcntl.LOCK_EX)
        try:
            os.mkdir(REQUEST_FIT_ENVELOPE_DIR_NAME, mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        envelope_fd = os.open(
            REQUEST_FIT_ENVELOPE_DIR_NAME,
            flags,
            dir_fd=root_fd,
        )
        root_stat = os.fstat(root_fd)
        envelope_stat = os.fstat(envelope_fd)
        locked = _LockedOutputDirectories(
            root_fd=root_fd,
            envelope_fd=envelope_fd,
            root_identity=(root_stat.st_dev, root_stat.st_ino),
            envelope_identity=(envelope_stat.st_dev, envelope_stat.st_ino),
        )
        _assert_locked_directory_identities(
            locked,
            output_root=output_root,
            envelope_dir=envelope_dir,
        )
        yield locked
    except OSError as exc:
        raise M1RequestFitPreparationError(
            f"could not anchor request-fit output directories safely: {exc}"
        ) from exc
    finally:
        if envelope_fd is not None:
            os.close(envelope_fd)
        if root_fd is not None:
            fcntl.flock(root_fd, fcntl.LOCK_UN)
            os.close(root_fd)


def _assert_locked_directory_identities(
    locked: _LockedOutputDirectories,
    *,
    output_root: Path,
    envelope_dir: Path,
) -> None:
    expected = (
        (output_root, locked.root_identity),
        (envelope_dir, locked.envelope_identity),
    )
    for path, identity in expected:
        try:
            observed = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise M1RequestFitPreparationError(
                f"request-fit output directory changed during preparation: {path.name}"
            ) from exc
        if (
            not stat.S_ISDIR(observed.st_mode)
            or (
                observed.st_dev,
                observed.st_ino,
            )
            != identity
        ):
            raise M1RequestFitPreparationError(
                f"request-fit output directory identity changed: {path.name}"
            )
    child = os.stat(
        REQUEST_FIT_ENVELOPE_DIR_NAME,
        dir_fd=locked.root_fd,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISDIR(child.st_mode)
        or (
            child.st_dev,
            child.st_ino,
        )
        != locked.envelope_identity
    ):
        raise M1RequestFitPreparationError(
            "request-fit envelope directory is no longer anchored to its output root"
        )


def _write_new_exact_at(directory_fd: int, name: str, payload: bytes) -> None:
    try:
        observed = _read_regular_at(directory_fd, name)
    except FileNotFoundError:
        observed = None
    if observed is not None:
        if observed != payload:
            raise M1RequestFitPreparationError(
                f"refusing to overwrite drifted request-fit artifact: {name}"
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
                raise M1RequestFitPreparationError(
                    f"refusing concurrent drift for request-fit artifact: {name}"
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
        raise M1RequestFitPreparationError(
            f"request-fit artifact must be a regular file: {name}"
        )
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
            raise M1RequestFitPreparationError(
                f"request-fit artifact changed while opening: {name}"
            )
        return handle.read()


def _read_regular_file(path: Path, *, context: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise M1RequestFitPreparationError(f"{context} must be a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise M1RequestFitPreparationError(f"could not read {context}: {exc}") from exc


def _decode_json_object(payload: bytes, *, context: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M1RequestFitPreparationError(f"could not decode {context}: {exc}") from exc
    if not isinstance(value, dict):
        raise M1RequestFitPreparationError(f"{context} must be a JSON object")
    return value


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
