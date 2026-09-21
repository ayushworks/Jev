from __future__ import annotations

import ast
import json
import math
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from judge_compare.artifact_cache import load_prior_manifest, materialize_artifact
from judge_compare.config import ExperimentConfig
from judge_compare.io import write_json_atomic, write_text_atomic
from judge_compare.paths import safe_join

S3_SUBMISSIONS = "https://sierra-tau-bench-public.s3.us-west-2.amazonaws.com/submissions"


def _github_raw(repository: str, commit: str, path: str) -> str:
    owner_repo = repository.removeprefix("https://github.com/").removesuffix(".git")
    return f"https://raw.githubusercontent.com/{owner_repo}/{commit}/{path}"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _normalized_task(value: Any) -> Any:
    """Remove known serialization-only defaults before release comparison."""
    if isinstance(value, dict):
        return {
            key: _normalized_task(child)
            for key, child in value.items()
            if not (key in {"requestor", "compare_args"} and child is None)
        }
    if isinstance(value, list):
        return [_normalized_task(child) for child in value]
    return value


def _project_to_reference_shape(value: Any, reference: Any, path: str = "task") -> Any:
    """Project richer serialized tasks onto the public release task schema."""
    if isinstance(reference, dict):
        if not isinstance(value, dict):
            raise ValueError(f"task shape mismatch at {path}: expected object")
        missing = set(reference) - set(value)
        if missing:
            raise ValueError(f"task fields missing at {path}: {sorted(missing)}")
        return {
            key: _project_to_reference_shape(value[key], child, f"{path}.{key}")
            for key, child in reference.items()
        }
    if isinstance(reference, list):
        if not isinstance(value, list) or len(value) != len(reference):
            raise ValueError(f"task list shape mismatch at {path}")
        return [
            _project_to_reference_shape(child, reference_child, f"{path}[{index}]")
            for index, (child, reference_child) in enumerate(zip(value, reference, strict=True))
        ]
    return value


def _extra_paths(value: Any, reference: Any, path: str = "task") -> set[str]:
    extras: set[str] = set()
    if isinstance(value, dict) and isinstance(reference, dict):
        extras.update(f"{path}.{key}" for key in set(value) - set(reference))
        for key in set(value) & set(reference):
            extras.update(_extra_paths(value[key], reference[key], f"{path}.{key}"))
    elif isinstance(value, list) and isinstance(reference, list):
        for index, (child, reference_child) in enumerate(zip(value, reference, strict=False)):
            extras.update(_extra_paths(child, reference_child, f"{path}[{index}]"))
    return extras


def _public_tool_signatures(path: Path) -> dict[str, list[str]]:
    """Read decorated tool names/arguments without importing benchmark code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    signatures: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "is_tool"
            for decorator in node.decorator_list
        )
        if decorated:
            signatures[node.name] = [arg.arg for arg in node.args.args if arg.arg != "self"]
    return dict(sorted(signatures.items()))


def _observed_tool_signatures(
    simulations: list[dict[str, Any]],
) -> dict[str, list[str]]:
    observed: dict[str, set[str]] = {}
    for simulation in simulations:
        for message in simulation.get("messages", []):
            for call in message.get("tool_calls") or []:
                observed.setdefault(str(call["name"]), set()).update(
                    str(key) for key in (call.get("arguments") or {})
                )
    return {name: sorted(arguments) for name, arguments in sorted(observed.items())}


def _numeric_summary(values: list[int | float]) -> dict[str, float | int | str] | None:
    if not values:
        return None
    ordered = sorted(values)

    def percentile(probability: float) -> int | float:
        return ordered[math.ceil((len(ordered) - 1) * probability)]

    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": percentile(0.5),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
        "max": ordered[-1],
        "mean": float(statistics.mean(ordered)),
        "percentile_method": "higher_order_statistic",
    }


def _last_assistant_text(simulation: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        message
        for message in simulation.get("messages", [])
        if message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ]
    return candidates[-1] if candidates else None


def _tool_pair_counts(simulations: list[dict[str, Any]]) -> dict[str, int]:
    calls = 0
    results = 0
    unmatched_calls = 0
    unmatched_results = 0
    duplicate_call_ids = 0
    duplicate_result_ids = 0
    missing_call_ids = 0
    missing_result_ids = 0
    out_of_order_results = 0
    null_result_contents = 0
    error_results = 0
    assistant_turns_with_multiple_calls = 0
    maximum_calls_in_assistant_turn = 0
    for simulation in simulations:
        messages = simulation.get("messages", [])
        call_positions: list[tuple[Any, int]] = []
        result_positions: list[tuple[Any, int]] = []
        for position, message in enumerate(messages):
            if message.get("role") == "assistant":
                message_calls = message.get("tool_calls") or []
                assistant_turns_with_multiple_calls += len(message_calls) > 1
                maximum_calls_in_assistant_turn = max(
                    maximum_calls_in_assistant_turn, len(message_calls)
                )
                call_positions.extend((call.get("id"), position) for call in message_calls)
            elif message.get("role") == "tool":
                result_positions.append((message.get("id"), position))
                null_result_contents += message.get("content") is None
                error_results += message.get("error") is True
        call_ids = [call_id for call_id, _ in call_positions]
        result_ids = [result_id for result_id, _ in result_positions]
        call_counts = Counter(call_ids)
        result_counts = Counter(result_ids)
        missing_call_ids += sum(
            call_id is None or (isinstance(call_id, str) and not call_id.strip())
            for call_id in call_ids
        )
        missing_result_ids += sum(
            result_id is None or (isinstance(result_id, str) and not result_id.strip())
            for result_id in result_ids
        )
        calls += sum(call_counts.values())
        results += sum(result_counts.values())
        unmatched_calls += sum((call_counts - result_counts).values())
        unmatched_results += sum((result_counts - call_counts).values())
        duplicate_call_ids += sum(max(0, count - 1) for count in call_counts.values())
        duplicate_result_ids += sum(max(0, count - 1) for count in result_counts.values())
        unique_call_positions = dict(call_positions)
        out_of_order_results += sum(
            result_id in unique_call_positions
            and result_position <= unique_call_positions[result_id]
            for result_id, result_position in result_positions
        )
    return {
        "tool_calls": calls,
        "tool_results": results,
        "unmatched_tool_calls": unmatched_calls,
        "unmatched_tool_results": unmatched_results,
        "duplicate_tool_call_ids": duplicate_call_ids,
        "duplicate_tool_result_ids": duplicate_result_ids,
        "missing_tool_call_ids": missing_call_ids,
        "missing_tool_result_ids": missing_result_ids,
        "out_of_order_tool_results": out_of_order_results,
        "null_tool_result_contents": null_result_contents,
        "error_tool_results": error_results,
        "assistant_turns_with_multiple_calls": assistant_turns_with_multiple_calls,
        "maximum_calls_in_assistant_turn": maximum_calls_in_assistant_turn,
    }


def _safe_sample(data: dict[str, Any]) -> dict[str, Any]:
    simulation = data["simulations"][0]
    target = _last_assistant_text(simulation)
    if target is None:
        raise ValueError("first source simulation has no assistant text")
    target_index = simulation["messages"].index(target)

    # This is a source-review sample, not a canonical evidence packet. It omits
    # raw provider payloads, costs, usage, hidden task instructions, and rewards.
    def message_view(message: dict[str, Any]) -> dict[str, Any]:
        allowed = ("id", "role", "content", "tool_calls", "requestor", "error", "turn_idx")
        return {key: message[key] for key in allowed if key in message}

    return {
        "notice": "M0 source-review sample; not a judge input and not a human-label packet",
        "simulation_id": simulation["id"],
        "task_id": simulation["task_id"],
        "trial": simulation["trial"],
        "termination_reason": simulation.get("termination_reason"),
        "policy": simulation.get("policy"),
        "conversation_through_target": [
            message_view(message) for message in simulation["messages"][: target_index + 1]
        ],
        "target_turn_idx": target.get("turn_idx"),
        "messages_after_target_omitted": len(simulation["messages"]) - target_index - 1,
    }


def audit_support_source(
    config: ExperimentConfig,
    *,
    raw_root: Path,
    manifest_path: Path,
    sample_path: Path,
    report_path: Path,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    submission = config.benchmark.source_submission
    trajectory_name = config.benchmark.source_trajectory_file
    source_dir = safe_join(raw_root, "tau2", submission)
    compatibility_dir = safe_join(source_dir, "compatibility", config.benchmark.commit)
    files = {
        "trajectory": (
            f"{S3_SUBMISSIONS}/{submission}/trajectories/{trajectory_name}",
            safe_join(source_dir, trajectory_name),
        ),
        "submission": (
            _github_raw(
                config.benchmark.repository,
                config.benchmark.commit,
                f"web/leaderboard/public/submissions/{submission}/submission.json",
            ),
            safe_join(compatibility_dir, "submission.json"),
        ),
        "license": (
            _github_raw(config.benchmark.repository, config.benchmark.commit, "LICENSE"),
            safe_join(compatibility_dir, "tau2-LICENSE"),
        ),
        "retail_policy": (
            _github_raw(
                config.benchmark.repository,
                config.benchmark.commit,
                "data/tau2/domains/retail/policy.md",
            ),
            safe_join(compatibility_dir, "retail-policy.md"),
        ),
        "retail_tools_source": (
            _github_raw(
                config.benchmark.repository,
                config.benchmark.commit,
                "src/tau2/domains/retail/tools.py",
            ),
            safe_join(compatibility_dir, "retail-tools.py"),
        ),
        "retail_tasks": (
            _github_raw(
                config.benchmark.repository,
                config.benchmark.commit,
                "data/tau2/domains/retail/tasks.json",
            ),
            safe_join(compatibility_dir, "retail-tasks.json"),
        ),
        "simulator_guidelines": (
            _github_raw(
                config.benchmark.repository,
                config.benchmark.commit,
                "data/tau2/user_simulator/simulation_guidelines.md",
            ),
            safe_join(compatibility_dir, "simulation-guidelines.md"),
        ),
    }
    retrieved_at_value = retrieved_at or datetime.now(UTC)
    prior_manifest = load_prior_manifest(manifest_path)
    prior_artifacts = (
        prior_manifest.get("artifacts", {}) if isinstance(prior_manifest, dict) else {}
    )
    if not isinstance(prior_artifacts, dict):
        raise ValueError("prior source manifest artifacts must be an object")
    prior_retrieved_at = str(prior_manifest.get("retrieved_at", "")) if prior_manifest else None
    artifacts: dict[str, dict[str, str | int]] = {}
    for name, (url, path) in files.items():
        previous = prior_artifacts.get(name)
        if previous is not None and not isinstance(previous, dict):
            raise ValueError(f"prior artifact entry must be an object: {name}")
        artifacts[name] = materialize_artifact(
            url=url,
            destination=path,
            prior=previous,
            retrieved_at=retrieved_at_value,
            prior_retrieved_at=prior_retrieved_at,
        )
        write_json_atomic(
            manifest_path,
            {
                "schema_version": "1.0",
                "audit_run_at": retrieved_at_value.isoformat(),
                "artifacts": artifacts,
                "findings": {"audit_status": "retrieved_pending_validation"},
            },
        )

    submission_data = _load_json(files["submission"][1])
    trajectory = _load_json(files["trajectory"][1])
    with files["retail_tasks"][1].open("r", encoding="utf-8") as handle:
        release_tasks_data = json.load(handle)
    tasks = trajectory.get("tasks", [])
    simulations = trajectory.get("simulations", [])
    if not isinstance(tasks, list) or not isinstance(simulations, list):
        raise ValueError("trajectory must contain list-valued tasks and simulations")

    info = trajectory.get("info", {})
    if not isinstance(info, dict):
        raise ValueError("trajectory info must be an object")
    task_counts = Counter(str(simulation.get("task_id")) for simulation in simulations)
    trial_counts = Counter(str(simulation.get("trial")) for simulation in simulations)
    simulation_ids = [simulation.get("id") for simulation in simulations]
    embedded_task_ids = [task.get("id") for task in tasks]
    task_trial_pairs = [
        (str(simulation.get("task_id")), str(simulation.get("trial")))
        for simulation in simulations
    ]
    termination_counts = Counter(
        str(simulation.get("termination_reason")) for simulation in simulations
    )
    role_counts = Counter(
        str(message.get("role"))
        for simulation in simulations
        for message in simulation.get("messages", [])
    )
    targets = [_last_assistant_text(simulation) for simulation in simulations]
    later_roles = Counter()
    contiguous_turn_histories = 0
    for simulation, target in zip(simulations, targets, strict=True):
        messages = simulation.get("messages", [])
        turn_indices = [message.get("turn_idx") for message in messages]
        contiguous_turn_histories += turn_indices == list(range(len(messages)))
        if target is None:
            later_roles["no_target"] += 1
            continue
        target_position = next(
            position for position, message in enumerate(messages) if message is target
        )
        roles = tuple(str(message.get("role")) for message in messages[target_position + 1 :])
        later_roles[",".join(roles) or "none"] += 1

    trial_seed_sets: dict[str, set[int]] = {}
    for simulation in simulations:
        trial = str(simulation.get("trial"))
        seed = simulation.get("seed")
        if isinstance(seed, int):
            trial_seed_sets.setdefault(trial, set()).add(seed)
    trial_seeds = {trial: sorted(seeds) for trial, seeds in sorted(trial_seed_sets.items())}
    target_prompt_tokens = [
        target["usage"]["prompt_tokens"]
        for target in targets
        if target is not None
        and isinstance(target.get("usage"), dict)
        and isinstance(target["usage"].get("prompt_tokens"), int)
    ]
    target_completion_tokens = [
        target["usage"]["completion_tokens"]
        for target in targets
        if target is not None
        and isinstance(target.get("usage"), dict)
        and isinstance(target["usage"].get("completion_tokens"), int)
    ]
    raw_agent_model_ids = Counter(
        str((message.get("raw_data") or {}).get("model"))
        for simulation in simulations
        for message in simulation.get("messages", [])
        if message.get("role") == "assistant"
        and isinstance(message.get("raw_data"), dict)
        and (message.get("raw_data") or {}).get("model")
    )
    raw_simulator_model_ids = Counter(
        str((message.get("raw_data") or {}).get("model"))
        for simulation in simulations
        for message in simulation.get("messages", [])
        if message.get("role") == "user"
        and isinstance(message.get("raw_data"), dict)
        and (message.get("raw_data") or {}).get("model")
    )
    reward_counts = Counter(
        str((simulation.get("reward_info") or {}).get("reward")) for simulation in simulations
    )

    public_tools = _public_tool_signatures(files["retail_tools_source"][1])
    observed_tools = _observed_tool_signatures(simulations)
    unknown_tools = sorted(set(observed_tools) - set(public_tools))
    unknown_argument_names = {
        name: sorted(set(arguments) - set(public_tools.get(name, [])))
        for name, arguments in observed_tools.items()
        if set(arguments) - set(public_tools.get(name, []))
    }
    raw_artifact_tasks_by_id = {str(task["id"]): task for task in tasks}
    release_tasks = (
        release_tasks_data.get("tasks", release_tasks_data)
        if isinstance(release_tasks_data, dict)
        else release_tasks_data
    )
    if not isinstance(release_tasks, list):
        raise ValueError("release tasks file must contain a list or a tasks list")
    raw_release_tasks_by_id = {str(task["id"]): task for task in release_tasks}
    release_tasks_by_id = {
        task_id: _normalized_task(task) for task_id, task in raw_release_tasks_by_id.items()
    }
    artifact_tasks_by_id = {
        task_id: _normalized_task(
            _project_to_reference_shape(raw_task, raw_release_tasks_by_id[task_id])
        )
        for task_id, raw_task in raw_artifact_tasks_by_id.items()
        if task_id in raw_release_tasks_by_id
    }
    task_extra_paths = sorted(
        set().union(
            *(
                _extra_paths(raw_task, raw_release_tasks_by_id[task_id])
                for task_id, raw_task in raw_artifact_tasks_by_id.items()
                if task_id in raw_release_tasks_by_id
            )
        )
    )
    compared_task_ids = set(artifact_tasks_by_id) | set(release_tasks_by_id)
    task_ids_match = set(raw_artifact_tasks_by_id) == set(release_tasks_by_id)
    matching_tasks = sum(
        artifact_tasks_by_id.get(task_id) == release_tasks_by_id.get(task_id)
        for task_id in compared_task_ids
    )
    embedded_guidelines = (info.get("user_info") or {}).get("global_simulation_guidelines", "")
    release_guidelines = files["simulator_guidelines"][1].read_text(encoding="utf-8")
    embedded_policy = (info.get("environment_info") or {}).get("policy", "")
    release_policy = files["retail_policy"][1].read_text(encoding="utf-8")
    agent_info = info.get("agent_info") or {}
    user_info = info.get("user_info") or {}
    submission_version = (submission_data.get("methodology") or {}).get("tau2_bench_version")
    tool_pairing = _tool_pair_counts(simulations)
    policies_nonempty = all(
        isinstance(simulation.get("policy"), str) and bool(simulation["policy"].strip())
        for simulation in simulations
    )
    per_simulation_policies_match = all(
        simulation.get("policy") == embedded_policy for simulation in simulations
    )

    metadata_checks = {
        "source_run_commit_matches_config": info.get("git_commit")
        == config.benchmark.source_run_commit,
        "source_agent_model_matches_config": agent_info.get("llm")
        == config.benchmark.source_agent_model,
        "source_simulator_model_matches_config": user_info.get("llm")
        == config.benchmark.source_simulator_model,
        "submission_version_matches_config": submission_version == config.benchmark.version,
    }
    compatibility = {
        "policy_byte_identical": embedded_policy == release_policy,
        "all_simulation_policies_match_embedded": per_simulation_policies_match,
        "simulator_guidelines_byte_identical": embedded_guidelines == release_guidelines,
        "task_ids_match": task_ids_match,
        "normalized_tasks_matching": matching_tasks,
        "normalized_tasks_total": len(compared_task_ids),
        "all_normalized_tasks_match": matching_tasks == len(compared_task_ids),
        "artifact_only_task_paths": task_extra_paths,
        "public_tool_count": len(public_tools),
        "observed_tool_count": len(observed_tools),
        "unknown_observed_tools": unknown_tools,
        "observed_unknown_argument_names": unknown_argument_names,
    }
    structural_checks = {
        "nonempty_tasks": bool(tasks),
        "nonempty_simulations": bool(simulations),
        "unique_nonblank_task_ids": len(embedded_task_ids) == len(set(embedded_task_ids))
        and all(task_id is not None and str(task_id).strip() for task_id in embedded_task_ids),
        "unique_nonblank_simulation_ids": len(simulation_ids) == len(set(simulation_ids))
        and all(
            simulation_id is not None and str(simulation_id).strip()
            for simulation_id in simulation_ids
        ),
        "unique_task_trial_pairs": len(task_trial_pairs) == len(set(task_trial_pairs)),
        "all_simulation_tasks_present": set(task_counts) == set(artifact_tasks_by_id),
        "every_task_has_declared_trial_count": bool(task_counts)
        and set(task_counts.values()) == {info.get("num_trials")},
        "one_seed_per_trial": len(trial_seeds) == info.get("num_trials")
        and all(len(seeds) == 1 for seeds in trial_seeds.values()),
        "all_turn_histories_contiguous": contiguous_turn_histories == len(simulations),
        "every_simulation_has_target": sum(target is not None for target in targets)
        == len(simulations),
        "targets_have_no_tool_calls": not any(
            target and target.get("tool_calls") for target in targets
        ),
        "exactly_one_terminal_user_message_after_target": later_roles
        == Counter({"user": len(simulations)}),
        "policies_nonempty": policies_nonempty,
        "tool_ids_unique_and_exactly_paired": all(
            tool_pairing[key] == 0
            for key in (
                "unmatched_tool_calls",
                "unmatched_tool_results",
                "duplicate_tool_call_ids",
                "duplicate_tool_result_ids",
                "missing_tool_call_ids",
                "missing_tool_result_ids",
                "out_of_order_tool_results",
            )
        ),
        "tool_results_have_content": tool_pairing["null_tool_result_contents"] == 0,
    }
    validation_failures = [
        f"metadata.{name}" for name, passed in metadata_checks.items() if not passed
    ]
    validation_failures.extend(
        f"structure.{name}" for name, passed in structural_checks.items() if not passed
    )
    compatibility_gate = {
        "policy_byte_identical": compatibility["policy_byte_identical"],
        "all_simulation_policies_match_embedded": compatibility[
            "all_simulation_policies_match_embedded"
        ],
        "simulator_guidelines_byte_identical": compatibility[
            "simulator_guidelines_byte_identical"
        ],
        "task_ids_match": compatibility["task_ids_match"],
        "all_normalized_tasks_match": compatibility["all_normalized_tasks_match"],
        "no_unknown_observed_tools": not unknown_tools,
        "no_unknown_observed_argument_names": not unknown_argument_names,
    }
    validation_failures.extend(
        f"compatibility.{name}" for name, passed in compatibility_gate.items() if not passed
    )

    findings = {
        "audit_status": "validated" if not validation_failures else "validation_failed",
        "validation_failures": validation_failures,
        "submission": submission,
        "submission_model": submission_data.get("model_name"),
        "submission_type": submission_data.get("submission_type"),
        "submission_tau2_version": submission_version,
        "source_git_commit": info.get("git_commit"),
        "configured_compatibility_commit": config.benchmark.commit,
        "source_revision_verified": config.benchmark.source_run_revision_verified,
        "source_revision_disposition": config.benchmark.source_run_revision_disposition,
        "metadata_checks": metadata_checks,
        "structural_checks": structural_checks,
        "agent_info": agent_info,
        "user_info": {
            key: value
            for key, value in user_info.items()
            if key != "global_simulation_guidelines"
        },
        "seed": info.get("seed"),
        "trial_seeds": trial_seeds,
        "declared_trials": info.get("num_trials"),
        "max_steps": info.get("max_steps"),
        "max_errors": info.get("max_errors"),
        "submission_verification": (submission_data.get("methodology") or {}).get(
            "verification"
        ),
        "submission_methodology_notes": (submission_data.get("methodology") or {}).get("notes"),
        "raw_agent_model_ids": dict(sorted(raw_agent_model_ids.items())),
        "raw_simulator_model_ids": dict(sorted(raw_simulator_model_ids.items())),
        "task_count": len(tasks),
        "simulation_count": len(simulations),
        "task_simulation_count_distribution": dict(
            sorted(Counter(task_counts.values()).items())
        ),
        "trial_counts": dict(sorted(trial_counts.items())),
        "termination_counts": dict(sorted(termination_counts.items())),
        "interaction_mode_counts": dict(
            sorted(Counter(str(simulation.get("mode")) for simulation in simulations).items())
        ),
        "benchmark_reward_counts_auxiliary_only": dict(sorted(reward_counts.items())),
        "source_final_turn_prompt_token_proxy": _numeric_summary(target_prompt_tokens),
        "source_target_completion_tokens": _numeric_summary(target_completion_tokens),
        "message_role_counts": dict(sorted(role_counts.items())),
        "simulations_with_candidate_target": sum(target is not None for target in targets),
        "candidate_target_has_tool_calls": sum(
            bool(target and target.get("tool_calls")) for target in targets
        ),
        "post_target_role_sequences": dict(sorted(later_roles.items())),
        "contiguous_turn_histories": contiguous_turn_histories,
        "unique_task_trial_pair_count": len(set(task_trial_pairs)),
        "tool_pairing": tool_pairing,
        "embedded_policy_present_for_all": policies_nonempty,
        "embedded_tool_definitions": (info.get("environment_info") or {}).get("tool_defs"),
        "compatibility": compatibility,
        "limitations": [
            "The trajectory advertises a source git commit that is not reachable from the public tau2-bench repository.",
            "The original public submission metadata names tau2-bench 0.2.1-dev; the selected public commit is a compatibility pin, not the verified generation commit.",
            "Submission metadata claims extended thinking for the source agent, but the trajectory agent settings record only temperature and no thinking budget; the exact reasoning configuration is not reconstructible.",
            "The raw artifact embeds the retail policy but sets environment_info.tool_defs to null.",
            "Tool definitions must be reconstructed from a reviewed compatible source pin and checked against every observed call.",
            "Every trajectory ends with a terminal user/simulator message after the last assistant response; packet construction must omit that future message.",
            "Task-family grouping beyond repeated trials is not provided and needs a documented derivation audit.",
            "The public S3 trajectory object is not content-addressed; its saved SHA-256 is the immutable local provenance anchor.",
            "The S3 trajectory has no embedded license field; redistribution coverage under the repository MIT license is not explicit.",
        ],
    }
    manifest = {
        "schema_version": "1.0",
        "audit_run_at": retrieved_at_value.isoformat(),
        "artifacts": artifacts,
        "findings": findings,
    }
    write_json_atomic(manifest_path, manifest)
    write_json_atomic(sample_path, _safe_sample(trajectory))
    write_text_atomic(report_path, render_source_report(findings, artifacts))
    if validation_failures:
        joined = ", ".join(validation_failures)
        raise ValueError(f"candidate source failed closed validation: {joined}")
    return manifest


def render_source_report(
    findings: dict[str, Any], artifacts: dict[str, dict[str, str | int]]
) -> str:
    pairing = findings["tool_pairing"]
    compatibility = findings["compatibility"]
    structural = findings["structural_checks"]
    metadata = findings["metadata_checks"]
    if findings["audit_status"] == "validated":
        target_summary = (
            "All required structural checks passed: every simulation has a deterministic "
            "last textual assistant target with no tool call, followed by exactly one "
            "terminal simulator/user message. That future message must not enter the "
            "evidence packet."
        )
        recommendation = (
            "Retain this source as the leading candidate because it is a standard published "
            "run with a complete 114-task x 4-trial conversation artifact, paired tool "
            "calls/results, embedded policies, fixed source model, and deterministic target "
            "responses. Approve it only with an explicit provenance limitation: the exact "
            "run commit is unavailable and tool schemas must be reconstructed and "
            "compatibility-tested against the pinned public release before G1."
        )
    else:
        target_summary = (
            "The source failed one or more required validation checks. Do not construct "
            "evidence packets until every listed failure is resolved."
        )
        recommendation = "Reject this candidate until the validation failures are resolved."
    lines = [
        "# Candidate customer-support source audit",
        "",
        "> Public-artifact inspection only. No support-agent or judge model calls were made.",
        "",
        f"Audit status: **{findings['audit_status']}**",
        f"Validation failures: `{findings['validation_failures']}`",
        "",
        "## Candidate",
        "",
        f"- Submission: `{findings['submission']}`",
        f"- Model: `{findings['submission_model']}`",
        f"- Submission type: `{findings['submission_type']}`",
        f"- Submission-declared tau2-bench version: `{findings['submission_tau2_version']}`",
        f"- Raw trajectory source commit: `{findings['source_git_commit']}`",
        f"- Proposed compatibility pin: `{findings['configured_compatibility_commit']}`",
        f"- Exact source revision verified: **{findings['source_revision_verified']}**",
        f"- Revision disposition: `{findings['source_revision_disposition']}`",
        f"- Agent: `{(findings['agent_info'] or {}).get('implementation')}` / `{(findings['agent_info'] or {}).get('llm')}`",
        f"- User simulator: `{(findings['user_info'] or {}).get('implementation')}` / `{(findings['user_info'] or {}).get('llm')}`",
        f"- Raw response model IDs (agent): `{findings['raw_agent_model_ids']}`",
        f"- Raw response model IDs (simulator): `{findings['raw_simulator_model_ids']}`",
        f"- Seed: `{findings['seed']}`",
        f"- Trial seeds: `{findings['trial_seeds']}`",
        f"- Max steps/errors: `{findings['max_steps']} / {findings['max_errors']}`",
        f"- Submission prompt verification: `{findings['submission_verification']}`",
        f"- Submission methodology note: `{findings['submission_methodology_notes']}`",
        f"- Config/artifact identity checks: `{metadata}`",
        "",
        "## Corpus inventory",
        "",
        f"- Tasks: **{findings['task_count']}**",
        f"- Simulations: **{findings['simulation_count']}**",
        f"- Declared trials: **{findings['declared_trials']}**",
        f"- Trials observed: `{findings['trial_counts']}`",
        f"- Terminations: `{findings['termination_counts']}`",
        f"- Interaction modes: `{findings['interaction_mode_counts']}`",
        f"- Benchmark rewards (auxiliary only, never labels): `{findings['benchmark_reward_counts_auxiliary_only']}`",
        f"- Message roles: `{findings['message_role_counts']}`",
        f"- Candidate last-assistant targets: **{findings['simulations_with_candidate_target']}**",
        f"- Candidate targets with tool calls: **{findings['candidate_target_has_tool_calls']}**",
        f"- Messages after candidate target: `{findings['post_target_role_sequences']}`",
        f"- Tool calls/results: **{pairing['tool_calls']} / {pairing['tool_results']}**",
        f"- Unmatched tool calls/results: **{pairing['unmatched_tool_calls']} / {pairing['unmatched_tool_results']}**",
        f"- Duplicate tool call/result IDs: **{pairing['duplicate_tool_call_ids']} / {pairing['duplicate_tool_result_ids']}**",
        f"- Missing tool call/result IDs: **{pairing['missing_tool_call_ids']} / {pairing['missing_tool_result_ids']}**",
        f"- Out-of-order tool results: **{pairing['out_of_order_tool_results']}**",
        f"- Tool results marked as errors: **{pairing['error_tool_results']}**",
        f"- Assistant turns with multiple calls (maximum): **{pairing['assistant_turns_with_multiple_calls']} ({pairing['maximum_calls_in_assistant_turn']})**",
        f"- Contiguous turn histories: **{findings['contiguous_turn_histories']} / {findings['simulation_count']}**",
        f"- Unique task/trial pairs: **{findings['unique_task_trial_pair_count']} / {findings['simulation_count']}**",
        f"- Embedded policy on every simulation: **{findings['embedded_policy_present_for_all']}**",
        f"- Embedded tool definitions: `{findings['embedded_tool_definitions']}`",
        f"- Source final-turn prompt-token proxy: `{findings['source_final_turn_prompt_token_proxy']}`",
        f"- Source target completion tokens: `{findings['source_target_completion_tokens']}`",
        f"- Required structural checks: `{structural}`",
        "",
        target_summary,
        "",
        "## Public-pin compatibility checks",
        "",
        f"- Embedded policy byte-identical to pin: **{compatibility['policy_byte_identical']}**",
        f"- Every per-simulation policy matches the embedded policy: **{compatibility['all_simulation_policies_match_embedded']}**",
        f"- Simulator guidelines byte-identical to pin: **{compatibility['simulator_guidelines_byte_identical']}**",
        f"- Task ID sets match: **{compatibility['task_ids_match']}**",
        f"- Normalized tasks matching: **{compatibility['normalized_tasks_matching']} / {compatibility['normalized_tasks_total']}**",
        f"- Artifact-only task serialization paths excluded from release-shape comparison: `{compatibility['artifact_only_task_paths']}`",
        f"- Observed/public tool counts: **{compatibility['observed_tool_count']} / {compatibility['public_tool_count']}**",
        f"- Unknown observed tools: `{compatibility['unknown_observed_tools']}`",
        f"- Unknown observed argument names: `{compatibility['observed_unknown_argument_names']}`",
        "",
        "## Saved provenance",
        "",
        "| Artifact | Bytes | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for name, artifact in artifacts.items():
        lines.append(f"| {name} | {artifact['size_bytes']} | `{artifact['sha256']}` |")
    lines.extend(["", "## Unresolved issues", ""])
    for limitation in findings["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(
        [
            "",
            "## Recommendation for G0",
            "",
            recommendation,
            "",
        ]
    )
    return "\n".join(lines)
