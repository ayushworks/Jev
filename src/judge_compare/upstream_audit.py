from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from judge_compare.artifact_cache import load_prior_manifest, materialize_artifact
from judge_compare.config import ExperimentConfig
from judge_compare.io import write_json_atomic, write_text_atomic
from judge_compare.paths import safe_join

ARCHIVE_DIR = "assets/benchmark-jev-luna-terra-sonnet/6d08df72-c878-458c-b7c5-a7824ee6e721"
ORACLE_DIR = (
    "assets/benchmark-jev-luna-terra-sonnet-oracle/6d08df72-c878-458c-b7c5-a7824ee6e721"
)

REQUIRED_PATHS = (
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "src/evals/judges/__init__.py",
    "src/evals/judges/system_one.py",
    "src/evals/judges/llm.py",
    "src/evals/judge_reliability.py",
    "src/evals/model.py",
    "analysis/common.py",
    f"{ARCHIVE_DIR}/benchmark.json",
    f"{ARCHIVE_DIR}/oracle-labels.json",
    f"{ORACLE_DIR}/accuracy.json",
    f"{ORACLE_DIR}/cost-and-latency.svg",
)


def _raw_url(repository: str, commit: str, path: str) -> str:
    prefix = "https://github.com/"
    if not repository.startswith(prefix):
        raise ValueError(f"unsupported upstream repository URL: {repository}")
    owner_repo = repository.removeprefix(prefix).removesuffix(".git")
    return f"https://raw.githubusercontent.com/{owner_repo}/{commit}/{path}"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _label_distribution(oracle: dict[str, Any]) -> dict[str, dict[str, int]]:
    criteria = ("is_grounded", "matches_search_expectation", "is_useful", "does_pass")
    output: dict[str, dict[str, int]] = {}
    for criterion in criteria:
        counts = Counter(str(label[criterion]) for label in oracle["labels"])
        output[criterion] = dict(sorted(counts.items()))
    return output


def _accuracy_checks(accuracy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for judge, result in accuracy["judges"].items():
        weighted_correct = sum(
            case["does_pass_accuracy"] * case["repetitions"] for case in result["cases"]
        )
        recomputed = weighted_correct / result["predictions"]
        archived = result["does_pass_accuracy"]
        checks[judge] = {
            "archived": archived,
            "recomputed_from_archived_case_aggregates": recomputed,
            "matches": abs(archived - recomputed) < 1e-12,
        }
    return checks


def _variance_ratio_checks(benchmark: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    comparisons = benchmark["analysis"]["comparison"]
    for judge, values in comparisons.items():
        quality = values["quality"]
        denominator = quality["jev_mean_variance"]
        recomputed = quality["judge_mean_variance"] / denominator if denominator else None
        archived = quality["judge_to_jev_variance_ratio"]
        output[judge] = {
            "archived": archived,
            "recomputed_from_archived_summary": recomputed,
            "matches": archived is not None
            and recomputed is not None
            and abs(archived - recomputed) < 1e-9,
        }
    return output


def _cost_latency_rows(readme: str) -> list[dict[str, str]]:
    match = re.search(r"### Cost\s+(.*?)(?:\n## |\Z)", readme, flags=re.DOTALL)
    if not match:
        return []
    rows: list[dict[str, str]] = []
    pattern = re.compile(
        r"^\|\s*([^|]+?)\s*\|\s*\$([0-9.]+)\s*\|\s*([0-9.]+)\s*s\s*\|\s*\$([0-9.]+)\s*\|$",
        flags=re.MULTILINE,
    )
    for row in pattern.finditer(match.group(1)):
        rows.append(
            {
                "judge": row.group(1).strip(),
                "average_cost_per_call_usd": row.group(2),
                "average_latency_seconds": row.group(3),
                "total_evaluator_cost_usd": row.group(4),
            }
        )
    return rows


def _locked_package_versions(path: Path) -> dict[str, str]:
    with path.open("rb") as handle:
        lock = tomllib.load(handle)
    wanted = {
        "deepagents",
        "langchain-openai",
        "langchain-typesafe",
        "langsmith",
        "openai",
    }
    return dict(
        sorted(
            (str(package["name"]), str(package["version"]))
            for package in lock.get("package", [])
            if package.get("name") in wanted
        )
    )


def audit_upstream(
    config: ExperimentConfig,
    *,
    archive_root: Path,
    manifest_path: Path,
    report_path: Path,
    reuse_path: Path,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """Audit only committed archive files; this function imports no model SDKs."""
    if config.upstream.audit_mode != "archived_no_model_calls":
        raise ValueError("upstream audit must use archived_no_model_calls")

    base = safe_join(archive_root, config.upstream.commit)
    retrieved_at_value = retrieved_at or datetime.now(UTC)
    prior_manifest = load_prior_manifest(manifest_path)
    prior_entries = (
        prior_manifest.get("artifacts", []) if isinstance(prior_manifest, dict) else []
    )
    if not isinstance(prior_entries, list):
        raise ValueError("prior upstream manifest artifacts must be a list")
    prior_by_relative = {
        str(entry["relative_path"]): entry
        for entry in prior_entries
        if isinstance(entry, dict) and "relative_path" in entry
    }
    prior_retrieved_at = str(prior_manifest.get("retrieved_at", "")) if prior_manifest else None
    artifacts: list[dict[str, Any]] = []
    for relative in REQUIRED_PATHS:
        destination = safe_join(base, *relative.split("/"))
        url = _raw_url(config.upstream.repository, config.upstream.commit, relative)
        entry = materialize_artifact(
            url=url,
            destination=destination,
            prior=prior_by_relative.get(relative),
            retrieved_at=retrieved_at_value,
            prior_retrieved_at=prior_retrieved_at,
        )
        entry["relative_path"] = relative
        artifacts.append(entry)
        write_json_atomic(
            manifest_path,
            {
                "schema_version": "1.0",
                "audit_mode": "archived_no_model_calls",
                "audit_run_at": retrieved_at_value.isoformat(),
                "artifacts": artifacts,
                "findings": {"audit_status": "retrieved_pending_validation"},
            },
        )

    benchmark = _load_json(base / f"{ARCHIVE_DIR}/benchmark.json")
    oracle = _load_json(base / f"{ARCHIVE_DIR}/oracle-labels.json")
    accuracy = _load_json(base / f"{ORACLE_DIR}/accuracy.json")
    readme = (base / "README.md").read_text(encoding="utf-8")

    frozen_questions = [case["inputs"]["question"] for case in benchmark["frozen_cases"]]
    label_questions = [label["question"] for label in oracle["labels"]]
    oracle_by_question = {label["question"]: label for label in oracle["labels"]}
    case_inventory = []
    for index, case in enumerate(benchmark["frozen_cases"]):
        question = case["inputs"]["question"]
        outputs = case.get("outputs") or {}
        label = oracle_by_question.get(question, {})
        case_inventory.append(
            {
                "case_index": index,
                "question": question,
                "answer_characters": len(str(outputs.get("answer", ""))),
                "tool_call_count": len(outputs.get("tool_calls") or []),
                "evidence_count": len(outputs.get("evidence") or []),
                "human_labels": {
                    criterion: label.get(criterion)
                    for criterion in (
                        "is_grounded",
                        "matches_search_expectation",
                        "is_useful",
                        "does_pass",
                    )
                },
            }
        )
    accuracy_checks = _accuracy_checks(accuracy)
    variance_checks = _variance_ratio_checks(benchmark)
    experiment_metadata = benchmark.get("experiment", {}).get("metadata", {})
    recorded_model_ids = experiment_metadata.get("judge_models") or {}
    evaluated_judge_keys = set(benchmark.get("analysis", {}).get("summary", {}))
    judge_models = {
        "jev": None,
        **{
            name: model_id
            for name, model_id in recorded_model_ids.items()
            if name in evaluated_judge_keys
        },
    }
    unused_metadata_model_ids = {
        name: model_id
        for name, model_id in recorded_model_ids.items()
        if name not in evaluated_judge_keys
    }
    analysis_definition = benchmark.get("analysis", {}).get("analysis", {})
    validation_checks = {
        "experiment_id_matches": benchmark.get("experiment_id")
        == config.upstream.experiment_id,
        "frozen_questions_match_oracle": sorted(frozen_questions) == sorted(label_questions),
        "archived_accuracy_arithmetic_matches": all(
            check["matches"] for check in accuracy_checks.values()
        ),
        "archived_variance_ratio_arithmetic_matches": all(
            check["matches"] for check in variance_checks.values()
        ),
    }
    findings = {
        "audit_status": "validated" if all(validation_checks.values()) else "failed",
        "validation_checks": validation_checks,
        "upstream_repository": config.upstream.repository,
        "audited_commit": config.upstream.commit,
        "published_experiment_id": config.upstream.experiment_id,
        "archived_experiment_id": benchmark.get("experiment_id"),
        "archived_experiment_git": experiment_metadata.get("git", {}),
        "archived_num_repetitions": experiment_metadata.get("num_repetitions"),
        "frozen_case_count": len(frozen_questions),
        "questions_match_oracle": sorted(frozen_questions) == sorted(label_questions),
        "case_inventory": case_inventory,
        "label_distribution": _label_distribution(oracle),
        "judge_model_ids": judge_models,
        "unused_metadata_model_ids": unused_metadata_model_ids,
        "locked_package_versions": _locked_package_versions(base / "uv.lock"),
        "judge_runtime_settings": {
            "llm_gateway": {
                "timeout_seconds": 180,
                "max_retries": 2,
                "temperature": "provider_gateway_default",
                "top_p": "provider_gateway_default",
                "seed": "provider_gateway_default",
                "max_tokens": "provider_gateway_default",
            },
            "jev": {
                "hosted_model_version": "not_recorded",
                "sampling_settings": "not_applicable_or_not_recorded",
            },
        },
        "archived_analysis_definition": analysis_definition,
        "accuracy_checks": accuracy_checks,
        "variance_ratio_checks": variance_checks,
        "cost_latency_rows_quoted_from_readme": _cost_latency_rows(readme),
        "raw_repetition_records_available": False,
        "raw_billing_records_available": False,
        "raw_latency_records_available": False,
        "license_file_present_at_pin": False,
        "limitations": [
            "The archive contains aggregate repetition statistics, not the raw 100 judgments per case.",
            "Accuracy can be checked only from archived case aggregates, not raw judge outputs.",
            "Variance ratios can be checked algebraically from archived summaries, not recomputed from scores.",
            "Published cost and latency values lack raw per-request billing and timing records in the archive.",
            "Each judge runs quality, does-pass, and choice evaluators (1,500 interactions across five cases and 100 repetitions), but published total cost divided by average cost is approximately 1,000 calls; the cost denominator likely excludes one evaluator and cannot be established from raw billing records.",
            "The archived experiment metadata identifies a different dirty commit than the audit pin.",
            "No LICENSE file or package license declaration is present at the audit pin.",
            "The hosted Jev service version is not recorded in the archive.",
        ],
    }
    manifest = {
        "schema_version": "1.0",
        "audit_mode": "archived_no_model_calls",
        "audit_run_at": retrieved_at_value.isoformat(),
        "artifacts": artifacts,
        "findings": findings,
    }
    write_json_atomic(manifest_path, manifest)
    write_text_atomic(report_path, render_audit_report(findings, artifacts))
    write_text_atomic(reuse_path, render_reuse_record(config))
    if not all(validation_checks.values()):
        failed = ", ".join(name for name, passed in validation_checks.items() if not passed)
        raise ValueError(f"upstream archive failed validation: {failed}")
    return manifest


def render_audit_report(findings: dict[str, Any], artifacts: list[dict[str, Any]]) -> str:
    labels = findings["label_distribution"]
    accuracy = findings["accuracy_checks"]
    ratios = findings["variance_ratio_checks"]
    costs = findings["cost_latency_rows_quoted_from_readme"]
    experiment_git = findings["archived_experiment_git"]

    lines = [
        "# Upstream archive audit",
        "",
        "> Offline audit only. No weather-agent or judge model calls were made.",
        "",
        f"Audit status: **{findings['audit_status']}**",
        f"Validation checks: `{findings['validation_checks']}`",
        "",
        "## Provenance",
        "",
        f"- Repository: `{findings['upstream_repository']}`",
        f"- Audited repository commit: `{findings['audited_commit']}`",
        f"- Published experiment ID: `{findings['published_experiment_id']}`",
        f"- Archived experiment ID matches: `{findings['archived_experiment_id'] == findings['published_experiment_id']}`",
        f"- Experiment metadata commit: `{experiment_git.get('commit', 'missing')}` (dirty: `{experiment_git.get('dirty', 'unknown')}`)",
        f"- Archived files hashed: `{len(artifacts)}`",
        "- Upstream license at audited pin: **not found**",
        "",
        "The repository is publicly readable, but the audited pin has no license file and its package metadata declares no license. Until the owner obtains permission or the upstream project adds applicable terms, this project treats the code as a conceptual reference and does not copy source modules.",
        "",
        "## Recoverable execution metadata",
        "",
        f"- Repetitions per frozen case: **{findings['archived_num_repetitions']}**",
        f"- Judge model IDs: `{findings['judge_model_ids']}`",
        f"- Model IDs present in metadata but absent from archived judge summaries: `{findings['unused_metadata_model_ids']}`",
        f"- Locked relevant packages: `{findings['locked_package_versions']}`",
        f"- Judge runtime settings: `{findings['judge_runtime_settings']}`",
        f"- Archived analysis definition: `{findings['archived_analysis_definition']}`",
        "",
        "The Jev hosted model/version was not recorded. The LLM wrapper sets a 180-second timeout and two retries; temperature, top-p, seed, and maximum tokens were left to provider/gateway defaults.",
        "",
        "## Frozen cases and human labels",
        "",
        f"- Distinct frozen cases: **{findings['frozen_case_count']}**",
        f"- Frozen-case questions match oracle questions: **{findings['questions_match_oracle']}**",
        "",
        "| Criterion | Human 0 | Human 1 |",
        "| --- | ---: | ---: |",
    ]
    for criterion, counts in labels.items():
        lines.append(f"| `{criterion}` | {counts.get('0', 0)} | {counts.get('1', 0)} |")
    lines.extend(
        [
            "",
            "### Case inventory",
            "",
            "| Case | Question | Tool calls | Evidence | Grounded | Search behavior | Useful | Overall |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in findings["case_inventory"]:
        human = case["human_labels"]
        question = str(case["question"]).replace("|", "\\|")
        lines.append(
            f"| {case['case_index']} | {question} | {case['tool_call_count']} | "
            f"{case['evidence_count']} | {human['is_grounded']} | "
            f"{human['matches_search_expectation']} | {human['is_useful']} | "
            f"{human['does_pass']} |"
        )
    lines.extend(
        [
            "",
            "All five responses are labeled grounded and useful. Only Dublin fails overall, because the archived oracle marks the required-search behavior as failed. The archive therefore has no human-labeled grounding failure and cannot establish grounding-failure detection.",
            "",
            "Five cases repeated 100 times remain five cases for generalization; the 500 decisions per judge are repeated measurements, not 500 independent examples.",
            "",
            "## Recoverable checks",
            "",
            "| Judge | Archived binary accuracy | Recomputed from case aggregates | Match |",
            "| --- | ---: | ---: | :---: |",
        ]
    )
    for judge, item in accuracy.items():
        lines.append(
            f"| {judge} | {item['archived']:.4f} | "
            f"{item['recomputed_from_archived_case_aggregates']:.4f} | {item['matches']} |"
        )
    lines.extend(
        [
            "",
            "| Comparator | Archived quality-variance ratio | Algebraic check from archived means | Match |",
            "| --- | ---: | ---: | :---: |",
        ]
    )
    for judge, item in ratios.items():
        lines.append(
            f"| {judge} | {item['archived']:.6f} | "
            f"{item['recomputed_from_archived_summary']:.6f} | {item['matches']} |"
        )
    lines.extend(
        [
            "",
            "These checks verify arithmetic consistency of archived aggregates only. Raw repeated scores are absent, so the variance estimates and bootstrap intervals cannot be independently recomputed.",
            "",
            "## Published cost and latency (quoted, not independently reproduced)",
            "",
            "| Judge | Average cost/call | Average latency | Total evaluator cost |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for item in costs:
        lines.append(
            f"| {item['judge']} | ${item['average_cost_per_call_usd']} | "
            f"{item['average_latency_seconds']} s | ${item['total_evaluator_cost_usd']} |"
        )
    lines.extend(["", "## Limitations", ""])
    for limitation in findings["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(
        [
            "",
            "## Audit conclusion",
            "",
            "The archive supports attribution of the published small weather study and limited consistency checks. It does not provide independent evidence for customer-support grounding performance. The new support experiment must use distinct cases, independent criterion labels, raw local request records, and separate primary and repeatability analyses.",
            "",
        ]
    )
    return "\n".join(lines)


def render_reuse_record(config: ExperimentConfig) -> str:
    return f"""# Upstream reuse and change record

Upstream reference: `{config.upstream.repository}` at
`{config.upstream.commit}`.

The audited pin does not contain a license file or package-license declaration.
Accordingly, the initial implementation copies no upstream source code. It uses
the public implementation only as a scientific and interface reference. This
decision must be revisited if explicit reuse permission or licensing terms are
obtained.

| Upstream area | Initial treatment | Planned support-study change |
| --- | --- | --- |
| Shared evidence builder | Conceptual reference | Canonical allowlisted packet containing policy, tools, conversation prefix, and target response |
| Jev questions | Conceptual reference | Atomic criterion-level PASS propositions with raw `p_pass` |
| LLM typed output | Conceptual reference | Provider-native structured output with the same criterion definitions and `p_pass` fields |
| Frozen-input runner | Conceptual reference | Durable local ledgers, grouped splits, explicit study/repetition/attempt IDs, approval and budget guards |
| Archived analysis | Preserve and attribute | Separate human failure detection, missing-result accounting, complete-evaluation cost/latency, and bounded repeatability |
| LangSmith | Optional interoperability only | Local artifacts remain sufficient for resume and offline reporting |

Every later copied or adapted code fragment must be recorded here with its
source path, applicable license or permission, and local destination.
"""
