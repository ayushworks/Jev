from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import judge_compare.request_fit_batch as request_fit_batch
from judge_compare.hashing import canonical_sha256, sha256_bytes
from judge_compare.m1 import PUBLIC_MANIFEST_NAMES, _prepare_m1_internal
from judge_compare.request_fit import JudgeRequestEnvelopes
from judge_compare.request_fit_batch import (
    EXPECTED_REQUEST_FIT_ENVELOPES,
    M1RequestFitPreparation,
    M1RequestFitPreparationError,
    _assert_artifacts_are_gitignored,
    _assert_private_request_fit_root,
    _preflight_request_fit_output,
    _prepare_m1_request_fit_internal,
    prepare_m1_request_fit,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _PreparedBatchFixture:
    m1: object
    result: M1RequestFitPreparation
    public_before: dict[str, tuple[bytes, int, int]]


@pytest.fixture(scope="module")
def prepared_batch(tmp_path_factory: pytest.TempPathFactory) -> _PreparedBatchFixture:
    output = tmp_path_factory.mktemp("m1-request-fit")
    m1 = _prepare_m1_internal(
        ROOT,
        packet_root=output / "m1-packets",
        manifest_root=output / "m1-manifests",
    )
    public_before = {
        key: (
            (m1.manifest_root / filename).read_bytes(),
            (m1.manifest_root / filename).stat().st_ino,
            (m1.manifest_root / filename).stat().st_mtime_ns,
        )
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    result = _prepare_m1_request_fit_internal(
        ROOT,
        m1=m1,
        output_root=output / "request-fit",
    )
    return _PreparedBatchFixture(
        m1=m1,
        result=result,
        public_before=public_before,
    )


def test_batch_prepares_all_448_non_exposed_canonical_envelopes(
    prepared_batch: _PreparedBatchFixture,
) -> None:
    result = prepared_batch.result
    manifest = result.manifest
    unsealed = dict(manifest)
    recorded_hash = unsealed.pop("manifest_hash")

    assert recorded_hash == canonical_sha256(unsealed)
    assert manifest["status"] == "canonical_envelopes_prepared_exact_counts_pending"
    assert manifest["network_access_used"] is False
    assert manifest["provider_sdk_calls_made"] == 0
    assert manifest["model_calls_made"] == 0
    assert manifest["token_count_calls_made"] == 0
    assert manifest["exact_token_counts_recorded"] == 0
    assert manifest["counts"] == {
        "canonical_packets": 456,
        "owner_exposure_exclusions": 8,
        "structurally_eligible_non_exposed": 448,
        "request_envelopes": 448,
        "gpt_exact_count_records": 0,
        "jev_exact_count_records": 0,
    }
    assert len(result.envelope_paths) == EXPECTED_REQUEST_FIT_ENVELOPES
    assert len(manifest["entries"]) == EXPECTED_REQUEST_FIT_ENVELOPES
    assert set(result.output_root.iterdir()) == {
        result.envelope_dir,
        result.manifest_path,
    }

    excluded_ids = {
        entry["example_id"] for entry in prepared_batch.m1.exclusion_manifest["entries"]
    }
    prepared_ids = {entry["example_id"] for entry in manifest["entries"]}
    assert not (excluded_ids & prepared_ids)
    assert len(prepared_ids) == EXPECTED_REQUEST_FIT_ENVELOPES

    for entry, path in zip(manifest["entries"], result.envelope_paths, strict=True):
        payload = path.read_bytes()
        envelope = JudgeRequestEnvelopes.model_validate_json(payload)
        assert path.name == f"{entry['example_id']}.json"
        assert entry["envelope_path"] == f"envelopes/{path.name}"
        assert sha256_bytes(payload) == entry["envelope_file_sha256"]
        assert len(payload) == entry["envelope_file_size_bytes"]
        assert envelope.example_id == entry["example_id"]
        assert envelope.packet_hash == entry["packet_hash"]
        assert envelope.envelope_hash == entry["envelope_hash"]
        assert entry["exact_token_count_status"] == "not_executed"


def test_batch_does_not_touch_public_m1_proposals(
    prepared_batch: _PreparedBatchFixture,
) -> None:
    m1 = prepared_batch.m1
    public_after = {
        key: (
            (m1.manifest_root / filename).read_bytes(),
            (m1.manifest_root / filename).stat().st_ino,
            (m1.manifest_root / filename).stat().st_mtime_ns,
        )
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    assert public_after == prepared_batch.public_before


def test_identical_batch_is_idempotent_without_rewriting_files(
    prepared_batch: _PreparedBatchFixture,
) -> None:
    result = prepared_batch.result
    before = {
        path: (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_size)
        for path in (*result.envelope_paths, result.manifest_path)
    }

    repeated = _prepare_m1_request_fit_internal(
        ROOT,
        m1=prepared_batch.m1,
        output_root=result.output_root,
    )

    assert repeated.manifest == result.manifest
    assert {
        path: (path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_size)
        for path in (*result.envelope_paths, result.manifest_path)
    } == before


def test_batch_refuses_existing_envelope_drift_before_any_write(
    prepared_batch: _PreparedBatchFixture,
    tmp_path: Path,
) -> None:
    drifted_root = tmp_path / "request-fit"
    shutil.copytree(
        prepared_batch.result.output_root,
        drifted_root,
        copy_function=os.link,
    )
    target = drifted_root / "envelopes" / prepared_batch.result.envelope_paths[0].name
    target.unlink()
    target.write_bytes(prepared_batch.result.envelope_paths[0].read_bytes() + b" ")
    manifest_before = (drifted_root / "manifest.json").read_bytes()

    with pytest.raises(
        M1RequestFitPreparationError,
        match="refusing to overwrite drifted request-fit artifact",
    ):
        _prepare_m1_request_fit_internal(
            ROOT,
            m1=prepared_batch.m1,
            output_root=drifted_root,
        )

    assert (drifted_root / "manifest.json").read_bytes() == manifest_before


def test_output_preflight_rejects_unexpected_files_without_writing(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "request-fit"
    envelope_dir = output_root / "envelopes"
    envelope_dir.mkdir(parents=True)
    unexpected = envelope_dir / "unexpected.json"
    unexpected.write_text("{}\n", encoding="utf-8")
    expected = envelope_dir / "expected.json"

    with pytest.raises(M1RequestFitPreparationError, match="unexpected files"):
        _preflight_request_fit_output(
            output_root,
            envelope_dir=envelope_dir,
            manifest_path=output_root / "manifest.json",
            expected_files={expected: b"{}\n", output_root / "manifest.json": b"{}\n"},
        )

    assert not expected.exists()
    assert unexpected.read_bytes() == b"{}\n"


def test_public_entrypoint_revalidates_m1_and_preserves_public_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / ".gitignore").write_text("data/packets/**/*\n", encoding="utf-8")
    manifest_root = tmp_path / "data" / "manifests"
    manifest_root.mkdir(parents=True)
    for filename in PUBLIC_MANIFEST_NAMES.values():
        (manifest_root / filename).write_text('{"unchanged":true}\n', encoding="utf-8")

    sentinel = SimpleNamespace(name="prepared")
    calls: list[object] = []

    def fake_prepare_m1(root: Path) -> object:
        calls.append(("prepare_m1", root))
        return object()

    def fake_internal(root: Path, **kwargs: object) -> object:
        calls.append(("internal", root, kwargs))
        return sentinel

    monkeypatch.setattr(request_fit_batch, "prepare_m1", fake_prepare_m1)
    monkeypatch.setattr(
        request_fit_batch,
        "_prepare_m1_request_fit_internal",
        fake_internal,
    )
    before = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in manifest_root.iterdir()
    }

    observed = prepare_m1_request_fit(tmp_path)

    assert observed is sentinel
    assert calls[0] == ("prepare_m1", tmp_path.resolve())
    assert calls[1][0:2] == ("internal", tmp_path.resolve())
    assert calls[1][2]["require_canonical_paths"] is True
    assert {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in manifest_root.iterdir()
    } == before


def test_public_entrypoint_detects_public_manifest_change_during_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / ".gitignore").write_text("data/packets/**/*\n", encoding="utf-8")
    manifest_root = tmp_path / "data" / "manifests"
    manifest_root.mkdir(parents=True)
    manifest_paths = []
    for filename in PUBLIC_MANIFEST_NAMES.values():
        path = manifest_root / filename
        path.write_text('{"unchanged":true}\n', encoding="utf-8")
        manifest_paths.append(path)

    monkeypatch.setattr(request_fit_batch, "prepare_m1", lambda _: object())

    def mutate_during_internal(*_: object, **__: object) -> object:
        manifest_paths[0].write_text('{"changed":true}\n', encoding="utf-8")
        return object()

    monkeypatch.setattr(
        request_fit_batch,
        "_prepare_m1_request_fit_internal",
        mutate_during_internal,
    )

    with pytest.raises(M1RequestFitPreparationError, match="changed during"):
        prepare_m1_request_fit(tmp_path)


def test_public_output_root_must_be_canonical_and_gitignored(tmp_path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / ".gitignore").write_text("data/packets/**/*\n", encoding="utf-8")
    canonical = tmp_path / "data" / "packets" / "v1" / "request-fit"

    _assert_private_request_fit_root(tmp_path, canonical)
    with pytest.raises(M1RequestFitPreparationError, match="only under"):
        _assert_private_request_fit_root(tmp_path, tmp_path / "elsewhere")

    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    with pytest.raises(M1RequestFitPreparationError, match="git ignore"):
        _assert_private_request_fit_root(tmp_path, canonical)


def test_every_real_artifact_path_must_be_gitignored_not_only_probes(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    output_root = tmp_path / "data" / "packets" / "v1" / "request-fit"
    manifest = output_root / "manifest.json"
    envelope = output_root / "envelopes" / f"cs-{'0' * 24}.json"
    (tmp_path / ".gitignore").write_text(
        "\n".join(
            (
                "data/packets/v1/request-fit/manifest.json",
                ("data/packets/v1/request-fit/envelopes/.request-fit-ignore-probe.json"),
                "",
            )
        ),
        encoding="utf-8",
    )

    # The early location/probe guard passes, but the exhaustive guard must not.
    _assert_private_request_fit_root(tmp_path, output_root)
    with pytest.raises(M1RequestFitPreparationError, match="not all covered"):
        _assert_artifacts_are_gitignored(tmp_path, (manifest, envelope))

    (tmp_path / ".gitignore").write_text("data/packets/**/*\n", encoding="utf-8")
    _assert_artifacts_are_gitignored(tmp_path, (manifest, envelope))

    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", manifest.relative_to(tmp_path).as_posix()],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(M1RequestFitPreparationError, match="must not be tracked"):
        _assert_artifacts_are_gitignored(tmp_path, (manifest, envelope))


def test_manifest_on_disk_matches_returned_hash_sealed_value(
    prepared_batch: _PreparedBatchFixture,
) -> None:
    observed = json.loads(prepared_batch.result.manifest_path.read_text(encoding="utf-8"))
    assert observed == prepared_batch.result.manifest
    unsealed = dict(observed)
    manifest_hash = unsealed.pop("manifest_hash")
    assert canonical_sha256(unsealed) == manifest_hash
