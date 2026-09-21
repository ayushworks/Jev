from __future__ import annotations

import json
import math
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from judge_compare.config import ExperimentConfig, load_config
from judge_compare.corpus_selection import (
    CandidateCase,
    PoolSelection,
    SelectedCase,
    assign_grouped_split,
    derive_retail_task_families,
    select_label_blind_pools,
)
from judge_compare.hashing import canonical_sha256, sha256_file
from judge_compare.io import write_json_atomic
from judge_compare.models import EvidencePacket
from judge_compare.packet_builder import (
    PINNED_RETAIL_TOOL_DEFINITIONS_SHA256,
    CorpusBuild,
    build_packet_corpus,
    write_packet_corpus,
)
from judge_compare.paths import safe_join

M1_SCHEMA_VERSION = "1.0"
EXPECTED_SOURCE_SIMULATIONS = 456
EXPECTED_SOURCE_TASKS = 114
EXPECTED_TRIALS_PER_TASK = 4
TIMING_EXPOSED_FAMILY = "retail-task-family-096"
PACKET_ROOT_RELATIVE = ("data", "packets", "v1")
HUMAN_AUDIT_REQUEST_RELATIVE = (
    "reviews",
    "M1-normalization-audit-request.yaml",
)
HUMAN_AUDIT_DECISION_RELATIVE = (
    "reviews",
    "M1-normalization-audit-decision.yaml",
)
HUMAN_AUDIT_INVENTORY_RELATIVE = (
    "audits",
    "M1-normalization-audit-reviewed-example-ids.json",
)
HUMAN_AUDIT_RESOLUTION_NAME = "m1-normalization-audit-resolution.json"
HUMAN_AUDIT_REQUIRED_COVERAGE = (
    "short_packet",
    "long_packet",
    "source_success_case",
    "source_failure_case",
)
HUMAN_AUDIT_REQUIRED_CHECKS = (
    "conversation_order_matches_source",
    "target_response_matches_source",
    "terminal_future_message_excluded",
    "tool_calls_and_results_preserved_and_paired",
    "policy_and_tool_definitions_match_pinned_source",
    "forbidden_fields_absent",
)
PUBLIC_MANIFEST_NAMES = {
    "corpus": "m1-corpus-manifest.json",
    "split": "m1-split-manifest.json",
    "pool": "m1-pool-manifest.json",
    "exclusion": "m1-exclusion-manifest.json",
}
PRIVATE_PUBLIC_KEYS = frozenset(
    {
        "private_traceability",
        "source_simulation_id",
        "source_simulation_hash",
        "source_provider_message_id",
        "source_call_id",
        "message_id_map",
        "tool_call_id_map",
        "excluded_terminal",
    }
)


class M1PreparationError(ValueError):
    """Raised when M1 cannot be prepared without changing reviewed inputs."""


@dataclass(frozen=True, slots=True)
class M1Preparation:
    corpus_manifest: dict[str, Any]
    split_manifest: dict[str, Any]
    pool_manifest: dict[str, Any]
    exclusion_manifest: dict[str, Any]
    packet_root: Path
    manifest_root: Path


@dataclass(frozen=True, slots=True)
class M1NormalizationAuditResolution:
    manifest: dict[str, Any]
    resolution_path: Path


@dataclass(frozen=True, slots=True)
class _G0Binding:
    config: ExperimentConfig
    decision: dict[str, Any]
    decision_hash: str
    package_hash: str
    active_config_hash: str
    source_manifest: dict[str, Any]
    source_manifest_hash: str


def prepare_m1(root: Path) -> M1Preparation:
    """Prepare the complete M1 packet frame and provisional pools offline.

    This function has no network or provider-SDK dependency. Existing outputs
    are accepted only when they are equivalent JSON values for the current
    deterministic build; otherwise preparation fails before writing anything.
    """

    resolved_root = root.resolve()
    output_packet_root = safe_join(resolved_root, *PACKET_ROOT_RELATIVE)
    output_manifest_root = safe_join(resolved_root, "data", "manifests")
    _assert_canonical_packet_root_is_ignored(resolved_root, output_packet_root)
    return _prepare_m1_internal(
        resolved_root,
        packet_root=output_packet_root,
        manifest_root=output_manifest_root,
    )


def record_m1_normalization_audit(
    root: Path,
) -> M1NormalizationAuditResolution:
    """Record an approved human packet-normalization audit append-only.

    The four M1 proposal manifests are read and validated but never rewritten.
    This operation makes no provider, model, or token-count calls and resolves
    only the human-audit blocker; exact context eligibility remains open.
    """

    resolved_root = root.resolve()
    packet_root = safe_join(resolved_root, *PACKET_ROOT_RELATIVE)
    manifest_root = safe_join(resolved_root, "data", "manifests")
    _assert_canonical_packet_root_is_ignored(resolved_root, packet_root)
    return _record_m1_normalization_audit_internal(
        resolved_root,
        packet_root=packet_root,
        manifest_root=manifest_root,
        decision_path=safe_join(
            resolved_root,
            *HUMAN_AUDIT_DECISION_RELATIVE,
        ),
        inventory_path=safe_join(
            packet_root,
            *HUMAN_AUDIT_INVENTORY_RELATIVE,
        ),
    )


def _prepare_m1_internal(
    root: Path,
    *,
    packet_root: Path,
    manifest_root: Path,
) -> M1Preparation:
    """Internal deterministic writer; public callers must use canonical roots."""

    resolved_root = root.resolve()
    output_packet_root = packet_root.resolve()
    output_manifest_root = manifest_root.resolve()
    binding = _validate_g0_binding(resolved_root)
    config = binding.config

    source_paths = _validated_local_source_paths(resolved_root, config, binding)
    trajectory_path = source_paths["trajectory"]
    policy_path = source_paths["retail_policy"]
    tools_path = source_paths["retail_tools_source"]
    tasks_path = source_paths["retail_tasks"]
    timing_path = safe_join(
        resolved_root, "data", "manifests", "annotation-timing-example.json"
    )
    timing_preview_path = safe_join(
        resolved_root, "reviews", "annotation-timing-example.md"
    )
    human_audit_request_path = safe_join(
        resolved_root, *HUMAN_AUDIT_REQUEST_RELATIVE
    )

    build = build_packet_corpus(
        trajectory_path=trajectory_path,
        policy_path=policy_path,
        tools_source_path=tools_path,
        compatibility_revision=config.benchmark.commit,
    )
    _validate_complete_build(build, binding=binding)

    source = _load_json_object(trajectory_path, context="trajectory source")
    release_tasks = _load_release_tasks(tasks_path)
    task_ids = [str(task["id"]) for task in release_tasks]
    family_by_task = derive_retail_task_families(task_ids)
    split_by_family = assign_grouped_split(
        family_by_task.values(),
        development_fraction=config.split.development_fraction,
        seed=config.split.seed,
    )

    entries_by_id = {
        str(item.manifest_entry["example_id"]): item.manifest_entry
        for item in build.packets
    }
    timing_record, timing_preview_sha256 = _validate_timing_exclusion(
        timing_path,
        preview_path=timing_preview_path,
        source=source,
        entries_by_id=entries_by_id,
        family_by_task=family_by_task,
        split_by_family=split_by_family,
    )
    excluded_family = str(timing_record["task_family_id"])
    exclusion_entries = [
        entry
        for entry in entries_by_id.values()
        if family_by_task[str(entry["task_id"])] == excluded_family
    ]
    if len(exclusion_entries) != EXPECTED_TRIALS_PER_TASK * 2:
        raise M1PreparationError(
            "timing-exposed family must contain exactly two tasks and eight trajectories"
        )

    structurally_eligible = [
        entry
        for entry in entries_by_id.values()
        if family_by_task[str(entry["task_id"])] != excluded_family
    ]
    candidates = [
        CandidateCase(
            example_id=str(entry["example_id"]),
            task_id=str(entry["task_id"]),
            trial=int(entry["trial"]),
            packet_length=int(entry["packet_size_bytes"]),
        )
        for entry in structurally_eligible
    ]

    scope = binding.decision["approved_scope"]
    calibration_scope = scope["annotation_calibration"]
    maximum_cases = config.execution.maximum_cases
    if not isinstance(maximum_cases, int):
        raise M1PreparationError("approved maximum_cases is unresolved")
    selection = select_label_blind_pools(
        candidates,
        family_by_task=family_by_task,
        split_by_family=split_by_family,
        seed=config.repeatability.selection_seed,
        model_judgment_cases=maximum_cases,
        model_development_cases=5,
        calibration_cases=int(calibration_scope["cases"]),
        calibration_minimum_families=int(calibration_scope["minimum_task_families"]),
    )
    _validate_exact_selection(selection, excluded_family=excluded_family)
    human_audit_request = _validate_human_normalization_audit_request(
        human_audit_request_path,
    )

    manifests = _build_public_manifests(
        root=resolved_root,
        build=build,
        binding=binding,
        family_by_task=family_by_task,
        split_by_family=split_by_family,
        selection=selection,
        timing_path=timing_path,
        timing_preview_sha256=timing_preview_sha256,
        timing_record=timing_record,
        human_audit_request=human_audit_request,
        exclusion_entries=exclusion_entries,
        structurally_eligible=structurally_eligible,
        packet_path_prefix=_output_path_for_manifest(
            resolved_root, output_packet_root
        ),
    )
    public_paths = {
        key: output_manifest_root / filename
        for key, filename in PUBLIC_MANIFEST_NAMES.items()
    }
    _preflight_private_outputs(build, output_packet_root)
    _preflight_public_outputs(public_paths, manifests)

    write_packet_corpus(build, output_packet_root)
    for key, path in public_paths.items():
        if not path.exists():
            write_json_atomic(path, manifests[key])

    return M1Preparation(
        corpus_manifest=manifests["corpus"],
        split_manifest=manifests["split"],
        pool_manifest=manifests["pool"],
        exclusion_manifest=manifests["exclusion"],
        packet_root=output_packet_root,
        manifest_root=output_manifest_root,
    )


def _record_m1_normalization_audit_internal(
    root: Path,
    *,
    packet_root: Path,
    manifest_root: Path,
    decision_path: Path,
    inventory_path: Path,
) -> M1NormalizationAuditResolution:
    """Internal resolution writer with injectable private paths for tests."""

    resolved_root = root.resolve()
    resolved_packet_root = packet_root.resolve()
    resolved_manifest_root = manifest_root.resolve()
    resolved_inventory_path = inventory_path.resolve()
    if not resolved_inventory_path.is_relative_to(resolved_packet_root):
        raise M1PreparationError(
            "reviewed-ID inventory must remain under the private packet root"
        )

    request_path = safe_join(
        resolved_root,
        *HUMAN_AUDIT_REQUEST_RELATIVE,
    )
    request_summary = _validate_human_normalization_audit_request(request_path)
    proposals = _load_and_validate_m1_proposals(
        resolved_root,
        manifest_root=resolved_manifest_root,
        request_summary=request_summary,
    )
    private_manifest_path = resolved_packet_root / "corpus_manifest.json"
    private_manifest = _validate_bound_private_corpus(
        resolved_root,
        packet_root=resolved_packet_root,
        private_manifest_path=private_manifest_path,
        corpus_proposal=proposals["corpus"],
    )
    decision = _validate_human_normalization_audit_decision(
        decision_path.resolve(),
        request_sha256=str(request_summary["request_sha256"]),
        private_manifest_hash=str(private_manifest["manifest_hash"]),
        proposal_manifest_hashes={
            key: str(proposals[key]["manifest_hash"])
            for key in PUBLIC_MANIFEST_NAMES
        },
    )
    inventory_sha256, reviewed_ids = _validate_reviewed_id_inventory(
        resolved_inventory_path,
        expected_sha256=str(decision["reviewed_example_ids_inventory_sha256"]),
        expected_private_manifest_hash=str(private_manifest["manifest_hash"]),
        expected_proposal_corpus_hash=str(proposals["corpus"]["manifest_hash"]),
        expected_count=int(decision["packets_reviewed"]),
        packet_root=resolved_packet_root,
        private_manifest=private_manifest,
        corpus_proposal=proposals["corpus"],
        pool_proposal=proposals["pool"],
    )
    resolution = _build_normalization_audit_resolution(
        root=resolved_root,
        request_path=request_path,
        decision_path=decision_path.resolve(),
        decision=decision,
        inventory_sha256=inventory_sha256,
        private_manifest_path=private_manifest_path,
        private_manifest=private_manifest,
        manifest_root=resolved_manifest_root,
        proposals=proposals,
        reviewed_count=len(reviewed_ids),
    )
    resolution_path = (
        resolved_manifest_root / HUMAN_AUDIT_RESOLUTION_NAME
    )
    _write_append_only_json(resolution_path, resolution)
    return M1NormalizationAuditResolution(
        manifest=resolution,
        resolution_path=resolution_path,
    )


def _validate_g0_binding(root: Path) -> _G0Binding:
    decision_path = safe_join(root, "reviews", "G0-decision.yaml")
    decision = _load_yaml_object(decision_path, context="G0 decision")
    if decision.get("gate") != "G0" or decision.get("decision") != "approved":
        raise M1PreparationError("prepare-m1 requires an approved G0 decision")

    artifact_hashes = decision.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise M1PreparationError("G0 decision has no artifact hash binding")
    required_bound_paths = {
        "reviews/G0-package.json",
        "reviews/G0-request.md",
        "configs/g0-gpt54.yaml",
        "protocol/protocol-v2.md",
    }
    if not required_bound_paths.issubset(artifact_hashes):
        missing = sorted(required_bound_paths - set(artifact_hashes))
        raise M1PreparationError(f"G0 decision is missing required bindings: {missing}")
    for relative in sorted(required_bound_paths):
        path = safe_join(root, *relative.split("/"))
        expected = artifact_hashes[relative]
        if not isinstance(expected, str) or sha256_file(path) != expected:
            raise M1PreparationError(f"G0-bound artifact hash mismatch: {relative}")

    active_path = safe_join(root, "configs", "experiment.yaml")
    proposal_path = safe_join(root, "configs", "g0-gpt54.yaml")
    expected_config_hash = artifact_hashes["configs/g0-gpt54.yaml"]
    if active_path.read_bytes() != proposal_path.read_bytes():
        raise M1PreparationError(
            "active configuration is not byte-identical to the G0-approved proposal"
        )
    active_config_hash = sha256_file(active_path)
    if active_config_hash != expected_config_hash:
        raise M1PreparationError("active configuration hash is not G0-approved")
    config = load_config(active_path)
    config.assert_g0_resolved()
    _validate_scope_matches_config(decision, config)

    package_path = safe_join(root, "reviews", "G0-package.json")
    package = _load_json_object(package_path, context="G0 package")
    package_entries = package.get("artifacts")
    if not isinstance(package_entries, list):
        raise M1PreparationError("G0 package has no artifact inventory")
    source_manifest_relative = "data/manifests/candidate_source_manifest.json"
    source_package_entries = [
        entry
        for entry in package_entries
        if isinstance(entry, dict) and entry.get("path") == source_manifest_relative
    ]
    if len(source_package_entries) != 1:
        raise M1PreparationError("G0 package does not uniquely bind the source manifest")
    source_manifest_path = safe_join(root, *source_manifest_relative.split("/"))
    source_manifest_hash = sha256_file(source_manifest_path)
    if source_manifest_hash != source_package_entries[0].get("sha256"):
        raise M1PreparationError("candidate source manifest changed after G0 review")
    source_manifest = _load_json_object(
        source_manifest_path, context="candidate source manifest"
    )
    if source_manifest.get("findings", {}).get("audit_status") != "validated":
        raise M1PreparationError("candidate source audit is not validated")

    return _G0Binding(
        config=config,
        decision=decision,
        decision_hash=sha256_file(decision_path),
        package_hash=sha256_file(package_path),
        active_config_hash=active_config_hash,
        source_manifest=source_manifest,
        source_manifest_hash=source_manifest_hash,
    )


def _validate_scope_matches_config(
    decision: Mapping[str, Any], config: ExperimentConfig
) -> None:
    scope = decision.get("approved_scope")
    if not isinstance(scope, Mapping):
        raise M1PreparationError("G0 decision has no approved scope")
    expected = {
        "quality_anchor": config.judges.frontier.model,
        "jev_model": config.judges.jev.model,
        "model_judgment_cases_maximum": config.execution.maximum_cases,
        "paid_request_attempts_maximum": config.execution.maximum_paid_requests,
        "attempts_per_judge_case_maximum": config.execution.maximum_attempts,
        "repeatability_enabled": config.repeatability.enabled,
    }
    mismatches = {
        key: {"decision": scope.get(key), "config": value}
        for key, value in expected.items()
        if scope.get(key) != value
    }
    if decision.get("approved_budget_usd") != config.execution.budget_usd:
        mismatches["approved_budget_usd"] = {
            "decision": decision.get("approved_budget_usd"),
            "config": config.execution.budget_usd,
        }
    if mismatches:
        raise M1PreparationError(f"active configuration differs from approved scope: {mismatches}")
    if scope.get("weather_execution_enabled") is not False:
        raise M1PreparationError("G0 scope must prohibit weather execution")
    if config.repeatability.enabled or config.repeatability.target_packets != 0:
        raise M1PreparationError("repeatability must be disabled for this M1 preparation")


def _validated_local_source_paths(
    root: Path,
    config: ExperimentConfig,
    binding: _G0Binding,
) -> dict[str, Path]:
    artifacts = binding.source_manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise M1PreparationError("candidate source manifest has no artifact mapping")
    required = {
        "trajectory",
        "retail_policy",
        "retail_tasks",
        "retail_tools_source",
    }
    if not required.issubset(artifacts):
        raise M1PreparationError(
            f"candidate source manifest is missing artifacts: {sorted(required - set(artifacts))}"
        )
    paths: dict[str, Path] = {}
    for name in sorted(required):
        entry = artifacts[name]
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise M1PreparationError(f"malformed source artifact entry: {name}")
        relative = Path(entry["path"])
        if relative.is_absolute():
            raise M1PreparationError(f"source artifact path must be local and relative: {name}")
        path = safe_join(root, *relative.parts)
        if not path.is_file():
            raise M1PreparationError(f"missing local source artifact: {name}")
        if path.stat().st_size != entry.get("size_bytes"):
            raise M1PreparationError(f"source artifact size mismatch: {name}")
        if sha256_file(path) != entry.get("sha256"):
            raise M1PreparationError(f"source artifact hash mismatch: {name}")
        paths[name] = path

    if paths["trajectory"].name != config.benchmark.source_trajectory_file:
        raise M1PreparationError("trajectory path does not match the active configuration")
    compatibility_component = config.benchmark.commit
    for name in ("retail_policy", "retail_tasks", "retail_tools_source"):
        if compatibility_component not in paths[name].parts:
            raise M1PreparationError(f"{name} is not from the pinned compatibility revision")
    return paths


def _validate_complete_build(build: CorpusBuild, *, binding: _G0Binding) -> None:
    manifest = build.manifest
    findings = binding.source_manifest.get("findings", {})
    trajectory_entry = binding.source_manifest["artifacts"]["trajectory"]
    expected_source_hash = trajectory_entry["sha256"]
    if manifest.get("source_sha256") != expected_source_hash:
        raise M1PreparationError("packet corpus source hash differs from the audited source")
    if manifest.get("source_simulation_count") != EXPECTED_SOURCE_SIMULATIONS:
        raise M1PreparationError("packet corpus does not contain exactly 456 source trajectories")
    if manifest.get("included_packet_count") != EXPECTED_SOURCE_SIMULATIONS:
        raise M1PreparationError("not every source trajectory produced exactly one packet")
    if len(build.packets) != EXPECTED_SOURCE_SIMULATIONS:
        raise M1PreparationError("packet object count does not reconcile with the source")
    if manifest.get("excluded_simulation_count") != 0:
        raise M1PreparationError("packet construction unexpectedly excluded source trajectories")
    if manifest.get("tool_definition_count") != 16:
        raise M1PreparationError("packet corpus must contain the 16 pinned retail tools")
    if manifest.get("tool_definitions_hash") != PINNED_RETAIL_TOOL_DEFINITIONS_SHA256:
        raise M1PreparationError("packet tool definitions do not match the pinned schema hash")
    if findings.get("simulation_count") != EXPECTED_SOURCE_SIMULATIONS:
        raise M1PreparationError("audited source simulation count changed")
    if findings.get("task_count") != EXPECTED_SOURCE_TASKS:
        raise M1PreparationError("audited source task count changed")
    if findings.get("task_simulation_count_distribution") != {
        str(EXPECTED_TRIALS_PER_TASK): EXPECTED_SOURCE_TASKS
    }:
        raise M1PreparationError("audited task/trial distribution changed")

    example_ids: set[str] = set()
    task_trials: set[tuple[str, int]] = set()
    for built in build.packets:
        payload = built.packet.model_dump(mode="json")
        EvidencePacket.model_validate(payload)
        entry = built.manifest_entry
        example_id = str(entry["example_id"])
        task_trial = (str(entry["task_id"]), int(entry["trial"]))
        if example_id in example_ids or task_trial in task_trials:
            raise M1PreparationError("packet corpus contains duplicate IDs or task/trial pairs")
        example_ids.add(example_id)
        task_trials.add(task_trial)
        if built.packet.schema_version != "1.0":
            raise M1PreparationError("unexpected packet schema version")
        if canonical_sha256(payload) != built.packet_hash:
            raise M1PreparationError(f"packet hash mismatch: {example_id}")
        canonical_size = len(
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if entry.get("packet_size_bytes") != canonical_size:
            raise M1PreparationError(f"canonical packet size mismatch: {example_id}")


def _load_release_tasks(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    tasks = value.get("tasks") if isinstance(value, dict) else value
    if not isinstance(tasks, list) or len(tasks) != EXPECTED_SOURCE_TASKS:
        raise M1PreparationError("pinned retail task frame must contain exactly 114 tasks")
    if not all(isinstance(task, dict) and "id" in task for task in tasks):
        raise M1PreparationError("pinned retail task frame contains malformed tasks")
    return tasks


def _validate_timing_exclusion(
    path: Path,
    *,
    preview_path: Path,
    source: Mapping[str, Any],
    entries_by_id: Mapping[str, Mapping[str, Any]],
    family_by_task: Mapping[str, str],
    split_by_family: Mapping[str, str],
) -> tuple[dict[str, Any], str]:
    record = _load_json_object(path, context="annotation timing example")
    preview_sha256 = _validate_timing_preview_hash(preview_path, record)
    required_values = {
        "exclusion_scope": "entire_task_family",
        "exposure_reason": "owner_visible_human_annotation_timing_and_training_example",
        "model_judgment_pool_eligible": False,
        "annotation_calibration_pool_eligible": False,
        "task_family_id": TIMING_EXPOSED_FAMILY,
    }
    mismatches = {
        key: {"expected": expected, "observed": record.get(key)}
        for key, expected in required_values.items()
        if record.get(key) != expected
    }
    if mismatches:
        raise M1PreparationError(f"invalid timing-exposure record: {mismatches}")
    example_id = record.get("example_id")
    if not isinstance(example_id, str) or example_id not in entries_by_id:
        raise M1PreparationError("timing-exposure example is not in the canonical corpus")
    entry = entries_by_id[example_id]
    task_id = str(entry["task_id"])
    exact_fields = {
        "packet_hash": entry["packet_hash"],
        "packet_size_bytes": entry["packet_size_bytes"],
        "prefix_message_count": entry["prefix_message_count"],
        "schema_version": "1.0",
        "source_simulation_hash": entry["source_simulation_hash"],
        "task_id": task_id,
        "trial": entry["trial"],
        "task_family_id": family_by_task[task_id],
        "split": split_by_family[family_by_task[task_id]],
    }
    field_mismatches = {
        key: {"expected": expected, "observed": record.get(key)}
        for key, expected in exact_fields.items()
        if record.get(key) != expected
    }
    if field_mismatches:
        raise M1PreparationError(f"timing-exposure packet binding mismatch: {field_mismatches}")

    simulations = source.get("simulations")
    if not isinstance(simulations, list):
        raise M1PreparationError("trajectory source has no simulations array")
    matching = [
        simulation
        for simulation in simulations
        if isinstance(simulation, dict)
        and str(simulation.get("task_id")) == task_id
        and simulation.get("trial") == entry["trial"]
    ]
    if len(matching) != 1:
        raise M1PreparationError("timing-exposure source trajectory is not unique")
    messages = matching[0].get("messages")
    if not isinstance(messages, list):
        raise M1PreparationError("timing-exposure source messages are missing")
    targets = [
        message
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ]
    if not targets:
        raise M1PreparationError("timing-exposure source target is missing")
    usage = targets[-1].get("usage")
    if not isinstance(usage, dict):
        raise M1PreparationError("timing-exposure source usage is missing")
    proxy_fields = {
        "source_prompt_token_proxy": usage.get("prompt_tokens"),
        "target_completion_token_proxy": usage.get("completion_tokens"),
    }
    if any(record.get(key) != value for key, value in proxy_fields.items()):
        raise M1PreparationError("timing-exposure source token proxies changed")
    return record, preview_sha256


def _validate_timing_preview_hash(
    preview_path: Path, record: Mapping[str, Any]
) -> str:
    expected = record.get("preview_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise M1PreparationError("timing-exposure preview hash is malformed")
    if not preview_path.is_file():
        raise M1PreparationError("timing-exposure preview is missing")
    observed = sha256_file(preview_path)
    if observed != expected:
        raise M1PreparationError(
            "timing-exposure preview hash does not match the recorded preview_sha256"
        )
    return observed


def _validate_human_normalization_audit_request(path: Path) -> dict[str, Any]:
    request = _load_yaml_object(
        path,
        context="M1 human normalization audit request",
    )
    expected = {
        "schema_version": M1_SCHEMA_VERSION,
        "gate": "M1-normalization-audit",
        "artifact_type": "immutable_public_audit_request",
        "status": "ready_for_human_review",
        "minimum_packets": 10,
        "required_coverage": list(HUMAN_AUDIT_REQUIRED_COVERAGE),
        "required_checks": list(HUMAN_AUDIT_REQUIRED_CHECKS),
        "review_sample_rule": {
            "split": "test",
            "minimum_distinct_task_families": 10,
            "exclude_any_family_in_provisional_pools": True,
            "excluded_task_families": [TIMING_EXPOSED_FAMILY],
        },
        "decision_record_path": Path(
            *HUMAN_AUDIT_DECISION_RELATIVE
        ).as_posix(),
        "private_reviewed_id_inventory_path": (
            Path(*PACKET_ROOT_RELATIVE, *HUMAN_AUDIT_INVENTORY_RELATIVE).as_posix()
        ),
        "resolution_manifest_path": (
            Path("data", "manifests", HUMAN_AUDIT_RESOLUTION_NAME).as_posix()
        ),
        "privacy": (
            "requirements_only_no_packet_content_rewards_source_ids_or_reviewed_ids"
        ),
    }
    mismatches = {
        key: {"expected": value, "observed": request.get(key)}
        for key, value in expected.items()
        if request.get(key) != value
    }
    unexpected = sorted(set(request) - set(expected) - {"instructions"})
    if mismatches or unexpected:
        raise M1PreparationError(
            "invalid M1 normalization audit request: "
            f"mismatches={mismatches}, unexpected={unexpected}"
        )
    instructions = request.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise M1PreparationError(
            "M1 normalization audit request requires review instructions"
        )
    return {
        "status": "open_at_proposal_time",
        "complete": False,
        "request_path": Path(*HUMAN_AUDIT_REQUEST_RELATIVE).as_posix(),
        "request_sha256": sha256_file(path),
        "decision_record_path": Path(
            *HUMAN_AUDIT_DECISION_RELATIVE
        ).as_posix(),
        "resolution_manifest_path": (
            Path("data", "manifests", HUMAN_AUDIT_RESOLUTION_NAME).as_posix()
        ),
    }


def _load_and_validate_m1_proposals(
    root: Path,
    *,
    manifest_root: Path,
    request_summary: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    expected_metadata = {
        "corpus": (
            "canonical_packet_corpus",
            "offline_packet_build_validated_m1_not_final",
        ),
        "split": (
            "grouped_split",
            "deterministic_m1_split_proposal_not_final",
        ),
        "pool": (
            "label_blind_pool_selection",
            "provisional_not_final_g1_blockers_open",
        ),
        "exclusion": (
            "corpus_exclusions",
            "recorded_m1_exclusion_proposal_not_final",
        ),
    }
    proposals: dict[str, dict[str, Any]] = {}
    for key, filename in PUBLIC_MANIFEST_NAMES.items():
        path = manifest_root / filename
        manifest = _load_json_object(path, context=f"M1 {key} proposal")
        _validate_sealed_manifest(manifest, context=f"M1 {key} proposal")
        expected_type, expected_status = expected_metadata[key]
        if (
            manifest.get("schema_version") != M1_SCHEMA_VERSION
            or manifest.get("milestone") != "M1"
            or manifest.get("manifest_type") != expected_type
            or manifest.get("status") != expected_status
        ):
            raise M1PreparationError(
                f"M1 {key} proposal metadata is not the immutable proposal form"
            )
        private_keys = _find_keys(manifest) & PRIVATE_PUBLIC_KEYS
        if private_keys:
            raise M1PreparationError(
                f"M1 {key} proposal exposes private keys: {sorted(private_keys)}"
            )
        proposals[key] = manifest

    corpus = proposals["corpus"]
    split = proposals["split"]
    pool = proposals["pool"]
    exclusion = proposals["exclusion"]
    if corpus.get("human_normalization_audit") != dict(request_summary):
        raise M1PreparationError(
            "M1 corpus proposal does not bind the immutable audit request"
        )
    required_blockers = [
        "exact_context_token_eligibility",
        "human_source_normalization_audit",
    ]
    for key, manifest in (("corpus", corpus), ("pool", pool)):
        blockers = manifest.get("g1_blockers")
        blocker_ids = (
            [item.get("blocker_id") for item in blockers]
            if isinstance(blockers, list)
            and all(isinstance(item, dict) for item in blockers)
            else None
        )
        if blocker_ids != required_blockers:
            raise M1PreparationError(
                f"M1 {key} proposal must retain both original G1 blockers"
            )
    if corpus.get("g1_blockers") != pool.get("g1_blockers"):
        raise M1PreparationError("M1 corpus and pool blockers differ")
    human_blocker = corpus["g1_blockers"][1]
    if (
        human_blocker.get("status") != "open_g1_blocker"
        or human_blocker.get("audit_request_sha256")
        != request_summary["request_sha256"]
        or human_blocker.get("human_signoff_present") is not False
    ):
        raise M1PreparationError(
            "M1 proposal human-audit blocker is not bound to the request"
        )
    if pool.get("final_pool_freeze") is not False:
        raise M1PreparationError("M1 pool proposal falsely claims a final freeze")

    corpus_hash = corpus["manifest_hash"]
    split_hash = split["manifest_hash"]
    exclusion_hash = exclusion["manifest_hash"]
    if split.get("bindings", {}).get("corpus_manifest_hash") != corpus_hash:
        raise M1PreparationError("M1 split proposal corpus binding is inconsistent")
    if (
        exclusion.get("bindings", {}).get("corpus_manifest_hash") != corpus_hash
        or exclusion.get("bindings", {}).get("split_manifest_hash") != split_hash
    ):
        raise M1PreparationError("M1 exclusion proposal bindings are inconsistent")
    if (
        pool.get("bindings", {}).get("corpus_manifest_hash") != corpus_hash
        or pool.get("bindings", {}).get("split_manifest_hash") != split_hash
        or pool.get("bindings", {}).get("exclusion_manifest_hash")
        != exclusion_hash
    ):
        raise M1PreparationError("M1 pool proposal bindings are inconsistent")

    common_binding_keys = {
        "g0_decision_sha256",
        "g0_package_sha256",
        "active_config_sha256",
        "candidate_source_manifest_sha256",
        "private_packet_corpus_manifest_hash",
    }
    corpus_bindings = corpus.get("bindings")
    if not isinstance(corpus_bindings, dict) or not common_binding_keys.issubset(
        corpus_bindings
    ):
        raise M1PreparationError("M1 corpus proposal is missing immutable bindings")
    expected_common = {
        key: corpus_bindings[key] for key in common_binding_keys
    }
    for key, manifest in proposals.items():
        bindings = manifest.get("bindings")
        if not isinstance(bindings, dict) or any(
            bindings.get(name) != value
            for name, value in expected_common.items()
        ):
            raise M1PreparationError(
                f"M1 {key} proposal common bindings are inconsistent"
            )

    current_g0 = _validate_g0_binding(root)
    current_bindings = {
        "g0_decision_sha256": current_g0.decision_hash,
        "g0_package_sha256": current_g0.package_hash,
        "active_config_sha256": current_g0.active_config_hash,
        "candidate_source_manifest_sha256": current_g0.source_manifest_hash,
    }
    if any(
        corpus_bindings.get(key) != value
        for key, value in current_bindings.items()
    ):
        raise M1PreparationError(
            "M1 proposals no longer bind the active approved G0 artifacts"
        )

    declared_private = corpus.get("private_traceability_location")
    if not isinstance(declared_private, str):
        raise M1PreparationError("M1 corpus proposal has no private manifest location")
    return proposals


def _validate_bound_private_corpus(
    root: Path,
    *,
    packet_root: Path,
    private_manifest_path: Path,
    corpus_proposal: Mapping[str, Any],
) -> dict[str, Any]:
    private_manifest = _load_json_object(
        private_manifest_path,
        context="private packet corpus manifest",
    )
    _validate_sealed_manifest(
        private_manifest,
        context="private packet corpus manifest",
    )
    expected_hash = corpus_proposal.get("bindings", {}).get(
        "private_packet_corpus_manifest_hash"
    )
    if private_manifest.get("manifest_hash") != expected_hash:
        raise M1PreparationError(
            "private packet corpus does not match the immutable M1 proposal"
        )
    declared = corpus_proposal.get("private_traceability_location")
    declared_path = Path(str(declared))
    if not declared_path.is_absolute():
        declared_path = root / declared_path
    if declared_path.resolve() != private_manifest_path.resolve():
        raise M1PreparationError(
            "M1 proposal private manifest path does not match the active packet root"
        )

    private_entries = private_manifest.get("entries")
    public_entries = corpus_proposal.get("entries")
    if not isinstance(private_entries, list) or not isinstance(public_entries, list):
        raise M1PreparationError("M1 corpus entries are malformed")
    private_by_id = _unique_entries_by_example_id(
        private_entries,
        context="private packet corpus",
    )
    public_by_id = _unique_entries_by_example_id(
        public_entries,
        context="public M1 corpus proposal",
    )
    if set(private_by_id) != set(public_by_id):
        raise M1PreparationError("public and private M1 corpus example IDs differ")
    for example_id, private_entry in private_by_id.items():
        public_entry = public_by_id[example_id]
        comparable = ("task_id", "trial", "packet_hash", "packet_size_bytes")
        if any(private_entry.get(key) != public_entry.get(key) for key in comparable):
            raise M1PreparationError(
                f"public/private M1 corpus entry mismatch: {example_id}"
            )
    if private_manifest.get("included_packet_count") != len(private_by_id):
        raise M1PreparationError("private packet corpus count is inconsistent")
    if corpus_proposal.get("counts", {}).get("canonical_packets") != len(
        public_by_id
    ):
        raise M1PreparationError("public M1 corpus count is inconsistent")
    if not private_manifest_path.resolve().is_relative_to(packet_root.resolve()):
        raise M1PreparationError("private corpus manifest is outside the packet root")
    return private_manifest


def _validate_human_normalization_audit_decision(
    path: Path,
    *,
    request_sha256: str,
    private_manifest_hash: str,
    proposal_manifest_hashes: Mapping[str, str],
) -> dict[str, Any]:
    decision = _load_yaml_object(
        path,
        context="M1 human normalization audit decision",
    )
    required_metadata = {
        "schema_version": M1_SCHEMA_VERSION,
        "gate": "M1-normalization-audit",
        "artifact_type": "human_audit_decision",
        "decision": "approved",
    }
    mismatches = {
        key: {"expected": value, "observed": decision.get(key)}
        for key, value in required_metadata.items()
        if decision.get(key) != value
    }
    if mismatches:
        raise M1PreparationError(
            "M1 normalization audit decision is not approved and valid: "
            f"{mismatches}"
        )
    allowed_keys = {
        *required_metadata,
        "reviewer",
        "reviewed_at",
        "conditions",
        "audit_request_sha256",
        "private_packet_corpus_manifest_hash",
        "proposal_manifest_hashes",
        "reviewed_example_ids_inventory_sha256",
        "packets_reviewed",
        "coverage",
        "checks",
        "notes",
    }
    unexpected = sorted(set(decision) - allowed_keys)
    if unexpected:
        raise M1PreparationError(
            f"normalization audit decision has unexpected fields: {unexpected}"
        )
    forbidden = _find_keys(decision) & (
        PRIVATE_PUBLIC_KEYS
        | {"example_ids", "reward", "rewards", "packet_content"}
    )
    if forbidden:
        raise M1PreparationError(
            f"normalization audit decision exposes forbidden fields: {sorted(forbidden)}"
        )
    reviewer = decision.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise M1PreparationError("approved normalization audit requires a reviewer")
    reviewed_at = decision.get("reviewed_at")
    if not _is_timezone_aware_iso8601(reviewed_at):
        raise M1PreparationError(
            "approved normalization audit requires a timezone-aware reviewed_at"
        )
    if decision.get("conditions") != []:
        raise M1PreparationError(
            "normalization audit conditions must be empty before resolution"
        )
    if decision.get("audit_request_sha256") != request_sha256:
        raise M1PreparationError(
            "normalization audit decision does not bind the immutable request"
        )
    if (
        decision.get("private_packet_corpus_manifest_hash")
        != private_manifest_hash
    ):
        raise M1PreparationError(
            "normalization audit decision does not bind the private packet corpus"
        )
    observed_proposals = decision.get("proposal_manifest_hashes")
    if observed_proposals != dict(proposal_manifest_hashes):
        raise M1PreparationError(
            "normalization audit decision does not bind all M1 proposals"
        )
    inventory_hash = decision.get("reviewed_example_ids_inventory_sha256")
    if not _is_sha256(inventory_hash):
        raise M1PreparationError(
            "approved normalization audit requires a valid reviewed-ID inventory hash"
        )
    packets_reviewed = decision.get("packets_reviewed")
    if type(packets_reviewed) is not int or packets_reviewed < 10:
        raise M1PreparationError(
            "approved normalization audit must cover at least 10 packets"
        )
    _validate_completed_boolean_map(
        decision.get("coverage"),
        required=HUMAN_AUDIT_REQUIRED_COVERAGE,
        context="required length/outcome coverage",
    )
    _validate_completed_boolean_map(
        decision.get("checks"),
        required=HUMAN_AUDIT_REQUIRED_CHECKS,
        context="required source/packet checks",
    )
    return decision


def _validate_reviewed_id_inventory(
    path: Path,
    *,
    expected_sha256: str,
    expected_private_manifest_hash: str,
    expected_proposal_corpus_hash: str,
    expected_count: int,
    packet_root: Path,
    private_manifest: Mapping[str, Any],
    corpus_proposal: Mapping[str, Any],
    pool_proposal: Mapping[str, Any],
) -> tuple[str, tuple[str, ...]]:
    if not path.is_file():
        raise M1PreparationError("private reviewed-ID inventory is missing")
    inventory_sha256 = sha256_file(path)
    if inventory_sha256 != expected_sha256:
        raise M1PreparationError(
            "private reviewed-ID inventory hash does not match the approved decision"
        )
    inventory = _load_json_object(
        path,
        context="private reviewed-ID inventory",
    )
    expected_keys = {
        "schema_version",
        "inventory_type",
        "private_packet_corpus_manifest_hash",
        "proposal_corpus_manifest_hash",
        "example_ids",
    }
    if set(inventory) != expected_keys:
        raise M1PreparationError("private reviewed-ID inventory schema is invalid")
    if (
        inventory.get("schema_version") != M1_SCHEMA_VERSION
        or inventory.get("inventory_type")
        != "m1_normalization_audit_reviewed_examples"
        or inventory.get("private_packet_corpus_manifest_hash")
        != expected_private_manifest_hash
        or inventory.get("proposal_corpus_manifest_hash")
        != expected_proposal_corpus_hash
    ):
        raise M1PreparationError("private reviewed-ID inventory bindings are invalid")
    example_ids = inventory.get("example_ids")
    if (
        not isinstance(example_ids, list)
        or not all(isinstance(value, str) and value.strip() for value in example_ids)
        or len(example_ids) != len(set(example_ids))
        or len(example_ids) != expected_count
        or len(example_ids) < 10
    ):
        raise M1PreparationError(
            "private reviewed-ID inventory must contain the approved count of distinct IDs"
        )

    private_entries = _unique_entries_by_example_id(
        private_manifest.get("entries"),
        context="private packet corpus",
    )
    public_entries = _unique_entries_by_example_id(
        corpus_proposal.get("entries"),
        context="public M1 corpus proposal",
    )
    model_pool = pool_proposal.get("model_judgment")
    calibration_pool = pool_proposal.get("annotation_calibration")
    if not isinstance(model_pool, list) or not isinstance(calibration_pool, list):
        raise M1PreparationError("M1 pool proposal entries are malformed")
    selected_entries = [*model_pool, *calibration_pool]
    if not all(
        isinstance(entry, Mapping)
        and isinstance(entry.get("task_family_id"), str)
        for entry in selected_entries
    ):
        raise M1PreparationError("M1 pool proposal contains malformed families")
    selected_families = {
        entry["task_family_id"] for entry in selected_entries
    }
    reviewed_families: set[str] = set()
    for example_id in example_ids:
        private_entry = private_entries.get(example_id)
        public_entry = public_entries.get(example_id)
        if private_entry is None or public_entry is None:
            raise M1PreparationError(
                f"reviewed example is absent from the bound corpus: {example_id}"
            )
        if private_entry.get("packet_hash") != public_entry.get("packet_hash"):
            raise M1PreparationError(
                f"reviewed packet hash differs across manifests: {example_id}"
            )
        family_id = public_entry.get("task_family_id")
        if (
            public_entry.get("split") != "test"
            or not isinstance(family_id, str)
            or family_id in selected_families
            or family_id == TIMING_EXPOSED_FAMILY
            or family_id in reviewed_families
        ):
            raise M1PreparationError(
                "normalization audit inventory must use distinct unselected test "
                "families and exclude the timing-exposed family"
            )
        reviewed_families.add(family_id)
        relative_packet = private_entry.get("packet_path")
        if not isinstance(relative_packet, str) or Path(relative_packet).is_absolute():
            raise M1PreparationError(
                f"reviewed packet has an invalid private path: {example_id}"
            )
        packet_path = safe_join(packet_root, *Path(relative_packet).parts)
        packet = _load_json_object(
            packet_path,
            context=f"reviewed packet {example_id}",
        )
        EvidencePacket.model_validate(packet)
        if packet.get("example_id") != example_id:
            raise M1PreparationError(
                f"reviewed packet example ID mismatch: {example_id}"
            )
        if canonical_sha256(packet) != private_entry.get("packet_hash"):
            raise M1PreparationError(
                f"reviewed packet content hash mismatch: {example_id}"
            )
    if len(reviewed_families) < 10:
        raise M1PreparationError(
            "normalization audit must cover at least 10 distinct unselected test families"
        )
    return inventory_sha256, tuple(example_ids)


def _build_normalization_audit_resolution(
    *,
    root: Path,
    request_path: Path,
    decision_path: Path,
    decision: Mapping[str, Any],
    inventory_sha256: str,
    private_manifest_path: Path,
    private_manifest: Mapping[str, Any],
    manifest_root: Path,
    proposals: Mapping[str, Mapping[str, Any]],
    reviewed_count: int,
) -> dict[str, Any]:
    proposal_bindings = {
        key: {
            "path": _output_path_for_manifest(
                root,
                manifest_root / PUBLIC_MANIFEST_NAMES[key],
            ),
            "file_sha256": sha256_file(
                manifest_root / PUBLIC_MANIFEST_NAMES[key]
            ),
            "manifest_hash": proposals[key]["manifest_hash"],
        }
        for key in PUBLIC_MANIFEST_NAMES
    }
    resolution = _seal_manifest(
        {
            "schema_version": M1_SCHEMA_VERSION,
            "milestone": "M1",
            "manifest_type": "human_normalization_audit_resolution",
            "status": "human_audit_resolved_m1_still_not_final",
            "privacy": "public_metadata_without_reviewed_ids_packets_or_rewards",
            "model_calls_made": 0,
            "network_access_used": False,
            "bindings": {
                "audit_request_path": _output_path_for_manifest(root, request_path),
                "audit_request_sha256": sha256_file(request_path),
                "audit_decision_path": _output_path_for_manifest(root, decision_path),
                "audit_decision_sha256": sha256_file(decision_path),
                "reviewed_example_ids_inventory_sha256": inventory_sha256,
                "private_packet_corpus_manifest_hash": private_manifest[
                    "manifest_hash"
                ],
                "private_packet_corpus_manifest_file_sha256": sha256_file(
                    private_manifest_path
                ),
                "proposal_manifests": proposal_bindings,
            },
            "review": {
                "reviewer": decision["reviewer"],
                "reviewed_at": decision["reviewed_at"],
                "conditions": [],
                "packets_reviewed": reviewed_count,
                "coverage": dict(decision["coverage"]),
                "checks": dict(decision["checks"]),
                "sample_blinding": {
                    "split": "test",
                    "distinct_unselected_task_families": reviewed_count,
                    "provisional_pool_family_overlap": 0,
                    "timing_exposed_family_overlap": 0,
                },
            },
            "resolved_g1_blocker": "human_source_normalization_audit",
            "remaining_g1_blockers": ["exact_context_token_eligibility"],
            "exact_context_resolution": {
                "status": "required_separate_resolution_not_recorded_here",
                "future_manifest": "data/manifests/m1-context-eligibility-resolution.json",
                "any_exclusion_requires_approved_amendment_before_reselection": True,
            },
            "m1_final_promotion_allowed": False,
            "final_promotion_requires": [
                HUMAN_AUDIT_RESOLUTION_NAME,
                "m1-context-eligibility-resolution.json",
            ],
        }
    )
    forbidden = _find_keys(resolution) & (
        PRIVATE_PUBLIC_KEYS
        | {"example_ids", "reward", "rewards", "packet_content"}
    )
    if forbidden:
        raise M1PreparationError(
            f"normalization audit resolution exposes forbidden fields: {sorted(forbidden)}"
        )
    return resolution


def _validate_sealed_manifest(
    manifest: Mapping[str, Any],
    *,
    context: str,
) -> None:
    content = dict(manifest)
    recorded = content.pop("manifest_hash", None)
    if not _is_sha256(recorded) or recorded != canonical_sha256(content):
        raise M1PreparationError(f"{context} has an invalid manifest hash")


def _unique_entries_by_example_id(
    entries: Any,
    *,
    context: str,
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(entries, list):
        raise M1PreparationError(f"{context} entries must be a list")
    values: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise M1PreparationError(f"{context} contains a malformed entry")
        example_id = entry.get("example_id")
        if not isinstance(example_id, str) or not example_id or example_id in values:
            raise M1PreparationError(
                f"{context} contains a duplicate or invalid example ID"
            )
        values[example_id] = entry
    return values


def _validate_completed_boolean_map(
    value: Any,
    *,
    required: Sequence[str],
    context: str,
) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != set(required)
        or not all(item is True for item in value.values())
    ):
        raise M1PreparationError(f"approved normalization audit lacks {context}")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_timezone_aware_iso8601(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_exact_selection(
    selection: PoolSelection,
    *,
    excluded_family: str,
) -> None:
    model = selection.model_judgment
    calibration = selection.annotation_calibration
    if len(model) != 21:
        raise M1PreparationError("model-judgment selection must contain exactly 21 cases")
    if Counter(case.split for case in model) != {"development": 5, "test": 16}:
        raise M1PreparationError("model-judgment selection must contain 5 dev and 16 test cases")
    if len({case.task_family_id for case in model}) != 21:
        raise M1PreparationError("model-judgment cases must use distinct task families")
    if len(calibration) != 24:
        raise M1PreparationError("annotation calibration selection must contain 24 cases")
    if {case.split for case in calibration} != {"development"}:
        raise M1PreparationError("annotation calibration cases must all be development cases")
    if len({case.task_family_id for case in calibration}) < 12:
        raise M1PreparationError("annotation calibration must span at least 12 families")
    model_ids = {case.example_id for case in model}
    calibration_ids = {case.example_id for case in calibration}
    model_families = {case.task_family_id for case in model}
    calibration_families = {case.task_family_id for case in calibration}
    if model_ids & calibration_ids or model_families & calibration_families:
        raise M1PreparationError("model and calibration pools overlap")
    if excluded_family in model_families | calibration_families:
        raise M1PreparationError("timing-exposed family entered a selected pool")


def _build_public_manifests(
    *,
    root: Path,
    build: CorpusBuild,
    binding: _G0Binding,
    family_by_task: Mapping[str, str],
    split_by_family: Mapping[str, str],
    selection: PoolSelection,
    timing_path: Path,
    timing_preview_sha256: str,
    timing_record: Mapping[str, Any],
    human_audit_request: Mapping[str, Any],
    exclusion_entries: Sequence[Mapping[str, Any]],
    structurally_eligible: Sequence[Mapping[str, Any]],
    packet_path_prefix: str,
) -> dict[str, dict[str, Any]]:
    config = binding.config
    excluded_ids = {str(entry["example_id"]) for entry in exclusion_entries}
    public_entries = []
    for built in sorted(
        build.packets,
        key=lambda item: (
            int(item.manifest_entry["task_id"]),
            int(item.manifest_entry["trial"]),
        ),
    ):
        entry = built.manifest_entry
        task_id = str(entry["task_id"])
        family_id = family_by_task[task_id]
        example_id = str(entry["example_id"])
        public_entries.append(
            {
                "example_id": example_id,
                "task_id": task_id,
                "trial": int(entry["trial"]),
                "task_family_id": family_id,
                "split": split_by_family[family_id],
                "packet_path": f"{packet_path_prefix}/packets/{example_id}.json",
                "packet_hash": entry["packet_hash"],
                "packet_size_bytes": int(entry["packet_size_bytes"]),
                "packet_schema_version": built.packet.schema_version,
                "pool_eligibility": (
                    "excluded_owner_exposure"
                    if example_id in excluded_ids
                    else "provisional_pending_exact_context_eligibility"
                ),
            }
        )

    sizes = [int(entry["packet_size_bytes"]) for entry in public_entries]
    context_blocker = {
        "blocker_id": "exact_context_token_eligibility",
        "status": "open_g1_blocker",
        "reason": (
            "Canonical packet bytes are known, but exact judge request serialization, "
            "the frozen rubric/instructions, and provider-specific token counts are not "
            "available offline at M1. Byte size is not substituted for token count."
        ),
        "required_resolution": (
            "Freeze each complete provider request envelope. After explicit authorization, "
            "count the full GPT-5.4 Responses input with POST /v1/responses/input_tokens, "
            "which includes formatting tokens, and use an exact supported Jev counter or "
            "provider validation. Apply both Jev limits and the GPT-5.4 input limit without "
            "truncation. If any context exclusion occurs, stop for an approved amendment "
            "before any reselection; the development allocation has zero family headroom."
        ),
        "affected_structurally_eligible_cases": len(structurally_eligible),
        "final_pool_freeze_allowed": False,
        "token_count_api_calls_made": 0,
    }
    human_audit_blocker = {
        "blocker_id": "human_source_normalization_audit",
        "status": "open_g1_blocker",
        "reason": (
            "The protocol-required direct human comparison of source trajectories against "
            "normalized packets has not been signed off. Automated schema and hash checks "
            "do not replace this review."
        ),
        "required_resolution": (
            "A human must privately inspect at least 10 source/packet pairs, covering short "
            "and long packets plus source-success and source-failure cases. Use distinct "
            "otherwise-unselected test families disjoint from both provisional pools and "
            "timing-exposed family 096, complete every normalization check, and sign a "
            "record bound to the private corpus manifest and a hash of the reviewed example "
            "inventory."
        ),
        "minimum_packets": 10,
        "required_coverage": [
            "short_packet",
            "long_packet",
            "source_success_case",
            "source_failure_case",
        ],
        "required_sample_rule": {
            "split": "test",
            "minimum_distinct_task_families": 10,
            "provisional_pool_family_overlap": 0,
            "timing_exposed_family_overlap": 0,
        },
        "audit_request_sha256": human_audit_request["request_sha256"],
        "audit_resolution_status": "not_recorded_in_proposal",
        "human_signoff_present": False,
        "final_m1_allowed": False,
    }
    g1_blockers = [context_blocker, human_audit_blocker]
    binding_block = {
        "g0_decision_sha256": binding.decision_hash,
        "g0_package_sha256": binding.package_hash,
        "active_config_sha256": binding.active_config_hash,
        "candidate_source_manifest_sha256": binding.source_manifest_hash,
        "private_packet_corpus_manifest_hash": build.manifest["manifest_hash"],
    }
    code_binding = {
        "packet_builder_sha256": sha256_file(
            safe_join(root, "src", "judge_compare", "packet_builder.py")
        ),
        "corpus_selection_sha256": sha256_file(
            safe_join(root, "src", "judge_compare", "corpus_selection.py")
        ),
        "m1_orchestration_sha256": sha256_file(
            safe_join(root, "src", "judge_compare", "m1.py")
        ),
        "task_family_review_sha256": sha256_file(
            safe_join(root, "protocol", "task-family-review.md")
        ),
    }

    corpus = _seal_manifest(
        {
            "schema_version": M1_SCHEMA_VERSION,
            "milestone": "M1",
            "manifest_type": "canonical_packet_corpus",
            "status": "offline_packet_build_validated_m1_not_final",
            "privacy": "public_metadata_without_private_traceability",
            "model_calls_made": 0,
            "network_access_used": False,
            "bindings": binding_block,
            "code_bindings": code_binding,
            "private_traceability_location": f"{packet_path_prefix}/corpus_manifest.json",
            "source_sha256": build.manifest["source_sha256"],
            "policy_sha256": build.manifest["policy_sha256"],
            "tools_source_sha256": build.manifest["tools_source_sha256"],
            "tool_definitions_hash": build.manifest["tool_definitions_hash"],
            "packet_schema_version": "1.0",
            "packet_size_metric": "canonical_json_utf8_bytes",
            "counts": {
                "source_trajectories": EXPECTED_SOURCE_SIMULATIONS,
                "canonical_packets": len(public_entries),
                "owner_exposure_exclusions": len(exclusion_entries),
                "structurally_eligible_after_exposure_exclusion": len(
                    structurally_eligible
                ),
                "exact_context_eligible": None,
                "exact_context_eligibility_pending": len(structurally_eligible),
            },
            "source_reconciliation": {
                "equation": "456 source = 448 structurally eligible + 8 exposure-excluded",
                "source_trajectories": EXPECTED_SOURCE_SIMULATIONS,
                "structurally_eligible": len(structurally_eligible),
                "exposure_excluded": len(exclusion_entries),
                "reconciled": (
                    EXPECTED_SOURCE_SIMULATIONS
                    == len(structurally_eligible) + len(exclusion_entries)
                ),
            },
            "packet_size_bytes_summary": _numeric_summary(sizes),
            "context_eligibility": {
                "status": "not_established_offline",
                "frontier_max_input_tokens": config.judges.frontier.max_input_tokens,
                "jev_total_request_limit_tokens": 64_000,
                "jev_state_plus_longest_question_limit_tokens": 32_000,
                "truncation_permitted": False,
                "shared_canonical_packet_for_both_judges": True,
                "offline_checks_completed": [
                    "canonical packet bytes computed",
                    "packet schemas validated",
                    "packet hashes validated",
                    "same packet selected for both judges",
                ],
                "offline_checks_not_possible_yet": [
                    (
                        "complete frozen GPT-5.4 request-envelope count including formatting "
                        "tokens; POST /v1/responses/input_tokens was not called"
                    ),
                    "complete frozen request state and longest-question token counts for Jev",
                ],
                "context_exclusion_rule": (
                    "Any context exclusion stops M1 for an approved amendment before "
                    "reselection; zero development-family headroom remains."
                ),
            },
            "human_normalization_audit": dict(human_audit_request),
            "g1_blockers": g1_blockers,
            "entries": public_entries,
        }
    )

    family_entries = [
        {
            "task_family_id": family_id,
            "task_ids": sorted(
                [task_id for task_id, value in family_by_task.items() if value == family_id],
                key=int,
            ),
            "split": split_by_family[family_id],
            "owner_exposure_excluded": family_id == TIMING_EXPOSED_FAMILY,
        }
        for family_id in sorted(set(family_by_task.values()))
    ]
    split_counts = Counter(entry["split"] for entry in family_entries)
    eligible_family_counts = Counter(
        entry["split"] for entry in family_entries if not entry["owner_exposure_excluded"]
    )
    split = _seal_manifest(
        {
            "schema_version": M1_SCHEMA_VERSION,
            "milestone": "M1",
            "manifest_type": "grouped_split",
            "status": "deterministic_m1_split_proposal_not_final",
            "privacy": "public_metadata_without_private_traceability",
            "bindings": {
                **binding_block,
                "corpus_manifest_hash": corpus["manifest_hash"],
                "family_mapping_hash": canonical_sha256(dict(family_by_task)),
            },
            "group_key": config.split.group_key,
            "development_fraction": config.split.development_fraction,
            "seed": config.split.seed,
            "assignment_method": "sha256_seeded_exact_family_count",
            "family_count": len(family_entries),
            "family_counts_by_split": dict(sorted(split_counts.items())),
            "eligible_family_counts_after_owner_exposure": dict(
                sorted(eligible_family_counts.items())
            ),
            "cross_split_family_overlap_count": 0,
            "entries": family_entries,
        }
    )

    exclusion_public_entries = [
        {
            "example_id": str(entry["example_id"]),
            "task_id": str(entry["task_id"]),
            "trial": int(entry["trial"]),
            "task_family_id": TIMING_EXPOSED_FAMILY,
            "split": split_by_family[TIMING_EXPOSED_FAMILY],
            "packet_hash": entry["packet_hash"],
            "packet_size_bytes": int(entry["packet_size_bytes"]),
            "primary_exclusion_reason": (
                "owner_visible_human_annotation_timing_and_training_example"
            ),
        }
        for entry in sorted(
            exclusion_entries,
            key=lambda value: (int(value["task_id"]), int(value["trial"])),
        )
    ]
    exclusion = _seal_manifest(
        {
            "schema_version": M1_SCHEMA_VERSION,
            "milestone": "M1",
            "manifest_type": "corpus_exclusions",
            "status": "recorded_m1_exclusion_proposal_not_final",
            "privacy": "public_metadata_without_private_traceability",
            "bindings": {
                **binding_block,
                "corpus_manifest_hash": corpus["manifest_hash"],
                "split_manifest_hash": split["manifest_hash"],
                "annotation_timing_record_sha256": sha256_file(timing_path),
                "annotation_timing_preview_sha256": timing_preview_sha256,
                "annotation_timing_example_packet_hash": timing_record["packet_hash"],
            },
            "counts": {
                "excluded_families": 1,
                "excluded_cases": len(exclusion_public_entries),
                "confirmed_context_exclusions": 0,
                "context_eligibility_pending": len(structurally_eligible),
            },
            "exclusion_scope": "entire_task_family",
            "entries": exclusion_public_entries,
        }
    )

    entries_by_id = {
        str(item.manifest_entry["example_id"]): item.manifest_entry
        for item in build.packets
    }
    model_pool = [
        _public_pool_entry(case, entries_by_id=entries_by_id, order=index)
        for index, case in enumerate(selection.model_judgment, start=1)
    ]
    calibration_pool = [
        _public_pool_entry(case, entries_by_id=entries_by_id, order=index)
        for index, case in enumerate(selection.annotation_calibration, start=1)
    ]
    pool = _seal_manifest(
        {
            "schema_version": M1_SCHEMA_VERSION,
            "milestone": "M1",
            "manifest_type": "label_blind_pool_selection",
            "status": "provisional_not_final_g1_blockers_open",
            "privacy": "public_metadata_without_private_traceability",
            "bindings": {
                **binding_block,
                "corpus_manifest_hash": corpus["manifest_hash"],
                "split_manifest_hash": split["manifest_hash"],
                "exclusion_manifest_hash": exclusion["manifest_hash"],
            },
            "selection_seed": config.repeatability.selection_seed,
            "selection_basis": "task_family_and_canonical_packet_size_bytes_without_labels",
            "outcome_fields_used": [],
            "final_pool_freeze": False,
            "g1_blockers": g1_blockers,
            "counts": {
                "model_judgment_cases": len(model_pool),
                "model_judgment_development_cases": sum(
                    entry["split"] == "development" for entry in model_pool
                ),
                "model_judgment_test_cases": sum(
                    entry["split"] == "test" for entry in model_pool
                ),
                "model_judgment_families": len(
                    {entry["task_family_id"] for entry in model_pool}
                ),
                "annotation_calibration_cases": len(calibration_pool),
                "annotation_calibration_families": len(
                    {entry["task_family_id"] for entry in calibration_pool}
                ),
                "case_overlap": 0,
                "family_overlap": 0,
            },
            "model_judgment": model_pool,
            "annotation_calibration": calibration_pool,
            "repeatability": {
                "status": "not_applicable",
                "reason": "disabled_at_g0",
                "selected_packets": 0,
                "planned_additional_judgments": 0,
                "additional_budget_usd": 0.0,
                "g0_decision_sha256": binding.decision_hash,
            },
        }
    )

    manifests = {
        "corpus": corpus,
        "split": split,
        "pool": pool,
        "exclusion": exclusion,
    }
    for name, manifest in manifests.items():
        private_keys = _find_keys(manifest) & PRIVATE_PUBLIC_KEYS
        if private_keys:
            raise M1PreparationError(
                f"public {name} manifest contains private traceability keys: "
                f"{sorted(private_keys)}"
            )
    return manifests


def _public_pool_entry(
    case: SelectedCase,
    *,
    entries_by_id: Mapping[str, Mapping[str, Any]],
    order: int,
) -> dict[str, Any]:
    entry = entries_by_id[case.example_id]
    return {
        "selection_order": order,
        "example_id": case.example_id,
        "task_id": case.task_id,
        "trial": case.trial,
        "task_family_id": case.task_family_id,
        "split": case.split,
        "pool": case.pool,
        "packet_hash": entry["packet_hash"],
        "packet_size_bytes": case.packet_length,
        "context_eligibility": "pending_exact_request_tokenization",
    }


def _seal_manifest(value: dict[str, Any]) -> dict[str, Any]:
    if "manifest_hash" in value:
        raise M1PreparationError("manifest content must not be pre-sealed")
    sealed = dict(value)
    sealed["manifest_hash"] = canonical_sha256(value)
    return sealed


def _numeric_summary(values: Sequence[int]) -> dict[str, int | str]:
    if not values:
        raise M1PreparationError("cannot summarize an empty packet-size frame")
    ordered = sorted(values)

    def percentile(probability: float) -> int:
        return ordered[math.ceil((len(ordered) - 1) * probability)]

    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": percentile(0.5),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
        "max": ordered[-1],
        "percentile_method": "higher_order_statistic",
    }


def _assert_canonical_packet_root_is_ignored(root: Path, packet_root: Path) -> None:
    canonical = safe_join(root, *PACKET_ROOT_RELATIVE)
    if packet_root.resolve() != canonical:
        raise M1PreparationError(
            "public M1 preparation may write private artifacts only under data/packets/v1"
        )
    private_paths = (
        Path(*PACKET_ROOT_RELATIVE, "corpus_manifest.json").as_posix(),
        Path(
            *PACKET_ROOT_RELATIVE,
            "packets",
            ".m1-ignore-probe.json",
        ).as_posix(),
        Path(
            *PACKET_ROOT_RELATIVE,
            "audits",
            ".m1-ignore-probe.json",
        ).as_posix(),
    )
    for private_path in private_paths:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", private_path],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise M1PreparationError(
                "data/packets/v1 private manifest, packet, and audit files must all "
                "be covered by the repository ignore rules"
            )


def _output_path_for_manifest(root: Path, output: Path) -> str:
    resolved = output.resolve()
    if resolved.is_relative_to(root):
        return resolved.relative_to(root).as_posix()
    return resolved.as_posix()


def _preflight_private_outputs(build: CorpusBuild, packet_root: Path) -> None:
    packet_dir = packet_root / "packets"
    expected_names = {f"{item.packet.example_id}.json" for item in build.packets}
    if packet_dir.exists():
        stale = {path.name for path in packet_dir.glob("*.json")} - expected_names
        if stale:
            raise M1PreparationError(
                f"private packet root contains unexpected JSON files: {sorted(stale)}"
            )
    for item in build.packets:
        path = packet_dir / f"{item.packet.example_id}.json"
        if path.exists():
            observed = _load_json_object(path, context=f"existing packet {item.packet.example_id}")
            expected = item.packet.model_dump(mode="json")
            if observed != expected:
                raise M1PreparationError(
                    f"refusing to overwrite drifted packet: {item.packet.example_id}"
                )
    private_manifest_path = packet_root / "corpus_manifest.json"
    if private_manifest_path.exists():
        observed = _load_json_object(
            private_manifest_path, context="existing private packet manifest"
        )
        if observed != build.manifest:
            raise M1PreparationError("refusing to overwrite drifted private packet manifest")


def _preflight_public_outputs(
    paths: Mapping[str, Path], manifests: Mapping[str, Mapping[str, Any]]
) -> None:
    for key, path in paths.items():
        if path.exists():
            observed = _load_json_object(path, context=f"existing public {key} manifest")
            if observed != manifests[key]:
                raise M1PreparationError(
                    f"refusing to overwrite drifted public M1 manifest: {path.name}"
                )


def _write_append_only_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        observed = _load_json_object(
            path,
            context=f"existing append-only resolution {path.name}",
        )
        if observed != value:
            raise M1PreparationError(
                f"refusing to overwrite drifted append-only resolution: {path.name}"
            )
        return
    write_json_atomic(path, value)


def _load_json_object(path: Path, *, context: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise M1PreparationError(f"could not load {context}: {error}") from error
    if not isinstance(value, dict):
        raise M1PreparationError(f"{context} must be a JSON object")
    return value


def _load_yaml_object(path: Path, *, context: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise M1PreparationError(f"could not load {context}: {error}") from error
    if not isinstance(value, dict):
        raise M1PreparationError(f"{context} must be a YAML object")
    return value


def _find_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for child in value.values():
            keys.update(_find_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_find_keys(child))
    return keys
