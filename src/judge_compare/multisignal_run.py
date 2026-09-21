from __future__ import annotations

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from judge_compare.benchmark_reward_run import (
    GPT_INPUT_PER_MILLION,
    GPT_MAX_OUTPUT_TOKENS,
    GPT_MODEL,
    GPT_OUTPUT_PER_MILLION,
    JEV_INPUT_PER_MILLION,
    JEV_MODEL,
    BenchmarkRunError,
    Case,
    load_cases,
)
from judge_compare.hashing import canonical_sha256
from judge_compare.io import write_json_atomic, write_text_atomic

QUESTIONS = {
    "task_success": (
        "Did the agent successfully complete the customer's requested outcome according "
        "to the evaluation criteria?"
    ),
    "policy_compliance": ("Did the agent comply with the supplied customer-support policy?"),
    "tool_correctness": (
        "Were the agent's tool calls and arguments correct for completing the customer's request?"
    ),
    "response_groundedness": (
        "Is the agent's final response fully supported by the conversation, tool calls, "
        "and tool results?"
    ),
    "customer_communication": (
        "Did the agent clearly and accurately communicate the outcome relevant to the "
        "customer's request?"
    ),
}

STATE_FRAMING = (
    "Evaluate the supplied customer-support trajectory against the supplied task and policy. "
    "Base judgments only on the supplied evidence."
)
GPT_FORMAT_INSTRUCTION = (
    "For every named question, return a probability between 0 and 1 that the answer is yes. "
    "Return only the required structured output."
)
BASELINE_COST_PER_REPETITION = 0.831813318


def prepare_cases(root: Path) -> list[Case]:
    prepared = []
    for case in load_cases(root):
        state = {
            "schema_version": "2.0",
            "evaluation_unit": "tau2_customer_support_multisignal",
            "evaluation_context": STATE_FRAMING,
            "benchmark_task": case.state["benchmark_task"],
            "trajectory_evidence": case.state["trajectory_evidence"],
        }
        prepared.append(replace(case, state=state, state_hash=canonical_sha256(state)))
    return prepared


def projected_cost(repetitions: int) -> dict[str, float]:
    if repetitions < 1:
        raise BenchmarkRunError("repetitions must be positive")
    return {
        "prior_run_usage_projection_usd": BASELINE_COST_PER_REPETITION * repetitions,
        "prior_run_usage_projection_with_20_percent_buffer_usd": (
            BASELINE_COST_PER_REPETITION * repetitions * 1.2
        ),
    }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load_env(root: Path) -> None:
    path = root / ".env"
    if not path.is_file():
        raise BenchmarkRunError(f"missing credential file: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    missing = [key for key in ("OPENAI_API_KEY", "TYPESAFE_API_KEY") if not os.getenv(key)]
    if missing:
        raise BenchmarkRunError(f"missing credentials: {', '.join(missing)}")


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            name: {"type": "number", "minimum": 0, "maximum": 1} for name in QUESTIONS
        },
        "required": list(QUESTIONS),
    }


def _validate_probabilities(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != set(QUESTIONS):
        raise BenchmarkRunError("judge response did not contain the five canonical signals")
    probabilities = {name: float(value[name]) for name in QUESTIONS}
    if any(not 0 <= probability <= 1 for probability in probabilities.values()):
        raise BenchmarkRunError("judge returned a probability outside [0, 1]")
    return probabilities


def _run_jev(case: Case, client: Any) -> tuple[dict[str, float], dict[str, Any], Any]:
    from typesafe_sdk import Noul

    response = client.system_one(
        state=case.state,
        questions={name: Noul(instructions=question) for name, question in QUESTIONS.items()},
    )
    probabilities = _validate_probabilities(
        {name: response.nouls[name].noul for name in QUESTIONS}
    )
    metadata = {
        "provider_request_id": response.request_id,
        "returned_model": response.model,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }
    return probabilities, metadata, json.loads(response.raw_http_response.content)


def _run_gpt(case: Case, client: Any) -> tuple[dict[str, float], dict[str, Any], Any]:
    response = client.responses.create(
        model=GPT_MODEL,
        instructions=GPT_FORMAT_INSTRUCTION,
        input=json.dumps({"questions": QUESTIONS, "state": case.state}, ensure_ascii=False),
        reasoning={"effort": "medium"},
        text={
            "format": {
                "type": "json_schema",
                "name": "customer_support_evaluation",
                "strict": True,
                "schema": _schema(),
            }
        },
        tools=[],
        store=False,
        service_tier="default",
        truncation="disabled",
        max_output_tokens=GPT_MAX_OUTPUT_TOKENS,
    )
    probabilities = _validate_probabilities(json.loads(response.output_text))
    details = getattr(response.usage, "output_tokens_details", None)
    metadata = {
        "provider_request_id": response.id,
        "returned_model": response.model,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "reasoning_tokens": getattr(details, "reasoning_tokens", 0) if details else 0,
        },
    }
    return probabilities, metadata, response.model_dump(mode="json")


def _cost(judge: str, usage: dict[str, int]) -> float:
    if judge == "jev":
        return usage["input_tokens"] / 1_000_000 * JEV_INPUT_PER_MILLION
    return (
        usage["input_tokens"] / 1_000_000 * GPT_INPUT_PER_MILLION
        + usage["output_tokens"] / 1_000_000 * GPT_OUTPUT_PER_MILLION
    )


def _request_reserve(case: Case, judge: str) -> float:
    input_bytes = len(
        json.dumps(
            {"questions": QUESTIONS, "state": case.state}, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
    )
    if judge == "jev":
        return input_bytes / 1_000_000 * JEV_INPUT_PER_MILLION
    return (
        input_bytes / 1_000_000 * GPT_INPUT_PER_MILLION
        + GPT_MAX_OUTPUT_TOKENS / 1_000_000 * GPT_OUTPUT_PER_MILLION
    )


def _mean_case_variance(rows: list[dict[str, Any]], signal: str) -> float:
    variances = []
    for order in sorted({row["order"] for row in rows}):
        values = [row["probabilities"][signal] for row in rows if row["order"] == order]
        variances.append(statistics.variance(values) if len(values) > 1 else 0.0)
    return statistics.fmean(variances)


def _binary_repeatability(rows: list[dict[str, Any]]) -> float:
    values = []
    for order in sorted({row["order"] for row in rows}):
        verdicts = [
            row["probabilities"]["task_success"] > 0.5 for row in rows if row["order"] == order
        ]
        pass_rate = statistics.fmean(verdicts)
        values.append(pass_rate**2 + (1 - pass_rate) ** 2)
    return statistics.fmean(values)


def analyze(results: list[dict[str, Any]], repetitions: int) -> dict[str, Any]:
    expected = 21 * repetitions
    metrics: dict[str, Any] = {}
    for judge in ("jev", "gpt"):
        rows = [row for row in results if row["judge"] == judge and row["status"] == "valid"]
        if len(rows) != expected:
            raise BenchmarkRunError(
                f"expected {expected} valid {judge} results, found {len(rows)}"
            )
        task_probabilities = [row["probabilities"]["task_success"] for row in rows]
        correct = [
            (row["probabilities"]["task_success"] > 0.5) == bool(row["reference_pass"])
            for row in rows
        ]
        brier = statistics.fmean(
            (probability - row["reference_pass"]) ** 2
            for probability, row in zip(task_probabilities, rows, strict=True)
        )
        case_summaries = []
        for order in range(1, 22):
            case_rows = [row for row in rows if row["order"] == order]
            pass_rate = statistics.fmean(
                row["probabilities"]["task_success"] > 0.5 for row in case_rows
            )
            reference_pass = case_rows[0]["reference_pass"]
            case_summaries.append(
                {
                    "order": order,
                    "example_id": case_rows[0]["example_id"],
                    "reference_pass": reference_pass,
                    "pass_rate": pass_rate,
                    "majority_correct": int(pass_rate > 0.5) == reference_pass,
                    "binary_verdict_varied": 0 < pass_rate < 1,
                }
            )
        metrics[judge] = {
            "judgments": len(rows),
            "accuracy": statistics.fmean(correct),
            "correct": sum(correct),
            "task_success_brier": brier,
            "task_success_mean_per_case_variance": _mean_case_variance(rows, "task_success"),
            "binary_repeatability": _binary_repeatability(rows),
            "majority_case_accuracy": statistics.fmean(
                row["majority_correct"] for row in case_summaries
            ),
            "cases_with_varying_binary_verdict": sum(
                row["binary_verdict_varied"] for row in case_summaries
            ),
            "case_summaries": case_summaries,
            "signal_mean_per_case_variance": {
                signal: _mean_case_variance(rows, signal) for signal in QUESTIONS
            },
            "mean_latency_seconds": statistics.fmean(row["latency_seconds"] for row in rows),
            "cost_usd": sum(row["cost_usd"] for row in rows),
        }
    return {"metrics": metrics}


def _report(analysis: dict[str, Any], metadata: dict[str, Any]) -> str:
    j, g = analysis["metrics"]["jev"], analysis["metrics"]["gpt"]
    rows = "\n".join(
        f"| {signal} | {j['signal_mean_per_case_variance'][signal]:.6f} | "
        f"{g['signal_mean_per_case_variance'][signal]:.6f} |"
        for signal in QUESTIONS
    )
    return f"""# Jev vs GPT-5.4: repeated multi-signal customer-support evaluation

## Design

Both judges received the same state and the same five atomic yes/no questions. Jev returned five native Noul probabilities; GPT-5.4 returned the same five probabilities through strict structured output. Only `task_success` was compared with the τ² binary reward; the other signals are diagnostic.

- Cases: 21
- Repetitions per case/judge: {metadata["repetitions"]}
- Valid judgments per judge: {21 * metadata["repetitions"]}
- Total cost: ${metadata["observed_cost_usd"]:.4f}

## Primary results

| Metric | Jev | GPT-5.4 |
|---|---:|---:|
| Task-success accuracy | {j["accuracy"]:.2%} | {g["accuracy"]:.2%} |
| Majority-vote case accuracy | {j["majority_case_accuracy"]:.2%} | {g["majority_case_accuracy"]:.2%} |
| Task-success Brier score | {j["task_success_brier"]:.4f} | {g["task_success_brier"]:.4f} |
| Task-success probability variance | {j["task_success_mean_per_case_variance"]:.6f} | {g["task_success_mean_per_case_variance"]:.6f} |
| Binary repeatability | {j["binary_repeatability"]:.4f} | {g["binary_repeatability"]:.4f} |
| Cases whose binary verdict varied | {j["cases_with_varying_binary_verdict"]} | {g["cases_with_varying_binary_verdict"]} |
| Mean latency | {j["mean_latency_seconds"]:.2f}s | {g["mean_latency_seconds"]:.2f}s |
| Cost | ${j["cost_usd"]:.4f} | ${g["cost_usd"]:.4f} |

## Mean per-case probability variance by signal

| Signal | Jev | GPT-5.4 |
|---|---:|---:|
{rows}

Accuracy counts repeated judgments against a fixed benchmark reward. Variance and repeatability measure stability on identical evidence. The 21-case corpus remains small, contains only six benchmark failures, and uses the τ² reward rather than a human oracle.
"""


def _execute_job(case: Case, repetition: int, judge: str) -> dict[str, Any]:
    from openai import OpenAI
    from typesafe_sdk import RetryPolicy, TypeSafeClient

    if judge == "jev":
        client = TypeSafeClient(model=JEV_MODEL, retry=RetryPolicy(max_retries=0), timeout=300)
        call = _run_jev
    else:
        client = OpenAI(max_retries=0, timeout=300)
        call = _run_gpt

    job_attempts = []
    for local_attempt in range(1, 4):
        started = time.monotonic()
        attempt = {
            "local_attempt": local_attempt,
            "started_at": _now(),
            "order": case.order,
            "example_id": case.example_id,
            "repetition": repetition,
            "judge": judge,
            "requested_model": JEV_MODEL if judge == "jev" else GPT_MODEL,
            "state_hash": case.state_hash,
            "questions_hash": canonical_sha256(QUESTIONS),
        }
        try:
            probabilities, provider, raw = call(case, client)
            attempt.update(
                status="valid",
                completed_at=_now(),
                latency_seconds=time.monotonic() - started,
                cost_usd=_cost(judge, provider["usage"]),
                **provider,
            )
            job_attempts.append(attempt)
            return {
                "attempts": job_attempts,
                "case": case,
                "probabilities": probabilities,
                "raw": raw,
            }
        except Exception as error:
            attempt.update(
                status="technical_error",
                completed_at=_now(),
                latency_seconds=time.monotonic() - started,
                cost_usd=_request_reserve(case, judge),
                error_type=type(error).__name__,
                error_message=str(error)[:1000],
            )
            job_attempts.append(attempt)
            if local_attempt == 3:
                return {"attempts": job_attempts, "case": case, "error": str(error)}
    raise AssertionError("unreachable")


def run(root: Path, *, repetitions: int, budget_usd: float, workers: int = 1) -> dict[str, Any]:
    if repetitions < 1:
        raise BenchmarkRunError("repetitions must be positive")
    if budget_usd <= 0:
        raise BenchmarkRunError("budget must be positive")
    if workers < 1 or workers > 12:
        raise BenchmarkRunError("workers must be between 1 and 12")
    projection = projected_cost(repetitions)
    buffered_projection = projection["prior_run_usage_projection_with_20_percent_buffer_usd"]
    if buffered_projection > budget_usd:
        raise BenchmarkRunError(
            f"the ${buffered_projection:.2f} buffered projection exceeds the "
            f"${budget_usd:.2f} budget; no provider calls were made"
        )
    _load_env(root)
    cases = prepare_cases(root)

    run_dir = root / f"runs/multisignal-r{repetitions}"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = run_dir / "attempts.json"
    results_path = run_dir / "results.json"
    attempts = json.loads(attempts_path.read_text()) if attempts_path.is_file() else []
    results = json.loads(results_path.read_text()) if results_path.is_file() else []
    completed = {
        (row["order"], row["repetition"], row["judge"])
        for row in results
        if row["status"] == "valid"
    }
    attempt_limit = 21 * repetitions * 2 * 3

    jobs = [
        (case, repetition, judge)
        for repetition in range(1, repetitions + 1)
        for case in cases
        for judge in ("jev", "gpt")
        if (case.order, repetition, judge) not in completed
    ]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(jobs), workers):
            batch = jobs[offset : offset + workers]
            if len(attempts) + len(batch) * 3 > attempt_limit:
                raise BenchmarkRunError(f"global {attempt_limit}-attempt ceiling reached")
            observed = sum(row.get("cost_usd", 0.0) for row in attempts)
            batch_reserve = sum(_request_reserve(case, judge) * 3 for case, _, judge in batch)
            if observed + batch_reserve > budget_usd:
                raise BenchmarkRunError(
                    f"next parallel batch could exceed the ${budget_usd:.2f} budget ceiling"
                )
            futures = {
                executor.submit(_execute_job, case, repetition, judge): (
                    case,
                    repetition,
                    judge,
                )
                for case, repetition, judge in batch
            }
            for future in as_completed(futures):
                case, repetition, judge = futures[future]
                outcome = future.result()
                for attempt in outcome["attempts"]:
                    attempt["attempt_number"] = len(attempts) + 1
                    attempts.append(attempt)
                write_json_atomic(attempts_path, attempts)
                if "error" in outcome:
                    raise BenchmarkRunError(
                        f"{judge} failed three times for repetition {repetition}, "
                        f"case {case.example_id}: {outcome['error']}"
                    )
                valid_attempt = outcome["attempts"][-1]
                raw_path = raw_dir / (
                    f"r{repetition:03d}-{case.order:02d}-{case.example_id}-{judge}.json"
                )
                write_json_atomic(raw_path, outcome["raw"])
                results.append(
                    {
                        **valid_attempt,
                        "reference_pass": case.reference_pass,
                        "probabilities": outcome["probabilities"],
                        "raw_response_path": str(raw_path.relative_to(root)),
                    }
                )
                write_json_atomic(results_path, results)
                print(
                    f"[{len(results):04d}/{21 * repetitions * 2}] r{repetition:03d} "
                    f"case {case.order:02d} {judge}: "
                    f"task_success={outcome['probabilities']['task_success']:.3f}",
                    flush=True,
                )

    analysis = analyze(results, repetitions)
    metadata = {
        "schema_version": "2.0",
        "completed_at": _now(),
        "repetitions": repetitions,
        "budget_usd": budget_usd,
        "observed_cost_usd": sum(row["cost_usd"] for row in results),
        "attempt_count": len(attempts),
        "question_wording": QUESTIONS,
        "questions_hash": canonical_sha256(QUESTIONS),
        "returned_models": {
            judge: sorted({row["returned_model"] for row in results if row["judge"] == judge})
            for judge in ("jev", "gpt")
        },
    }
    artifact = {"metadata": metadata, **analysis}
    write_json_atomic(run_dir / "analysis.json", artifact)
    write_text_atomic(
        root / "reports/multisignal-repeated-comparison.md", _report(analysis, metadata)
    )
    return artifact
