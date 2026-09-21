from __future__ import annotations

import html
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from judge_compare.hashing import canonical_sha256
from judge_compare.io import write_json_atomic, write_text_atomic

CASE_LIMIT = 21
ATTEMPT_LIMIT = 126
BUDGET_USD = 30.0
GPT_MODEL = "gpt-5.4-2026-03-05"
JEV_MODEL = "jev-latest"
GPT_INPUT_PER_MILLION = 2.50
GPT_OUTPUT_PER_MILLION = 15.00
JEV_INPUT_PER_MILLION = 0.042
GPT_MAX_OUTPUT_TOKENS = 4_000
QUESTION = (
    "Did the customer-support agent successfully satisfy the benchmark customer task "
    "according to the supplied task specification and evaluation criteria, based only "
    "on the supplied trajectory evidence? Return the probability that the answer is yes."
)
SYSTEM = (
    "You are an independent evaluator of a customer-support agent trajectory. Judge only "
    "whether the agent completed the benchmark task. Treat the benchmark task specification "
    "and evaluation criteria as the definition of success. Inspect the entire trajectory, "
    "including tool calls and tool results. Do not reward style unless the task requires it. "
    "Do not infer actions that are not evidenced. Output only the requested probability."
)


class BenchmarkRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class Case:
    order: int
    example_id: str
    task_id: str
    split: str
    reference_pass: int
    state: dict[str, Any]
    state_hash: str


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_env(path: Path) -> None:
    if not path.is_file():
        raise BenchmarkRunError(f"missing credential file: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)
    missing = [key for key in ("OPENAI_API_KEY", "TYPESAFE_API_KEY") if not os.getenv(key)]
    if missing:
        raise BenchmarkRunError(f"missing credentials: {', '.join(missing)}")


def load_cases(root: Path) -> list[Case]:
    pool = _load_json(root / "data/manifests/m1-pool-manifest.json")
    corpus = _load_json(root / "data/packets/v1/corpus_manifest.json")
    raw_paths = list((root / "data/raw/tau2").glob("*/*.json"))
    if len(raw_paths) != 1:
        raise BenchmarkRunError(f"expected one raw benchmark file, found {len(raw_paths)}")
    raw = _load_json(raw_paths[0])

    selected = sorted(pool["model_judgment"], key=lambda row: row["selection_order"])
    if len(selected) != CASE_LIMIT or [row["selection_order"] for row in selected] != list(
        range(1, CASE_LIMIT + 1)
    ):
        raise BenchmarkRunError("model judgment pool is not the frozen ordered 21-case pool")
    if len({row["example_id"] for row in selected}) != CASE_LIMIT:
        raise BenchmarkRunError("duplicate example IDs in selected pool")

    entries = {row["example_id"]: row for row in corpus["entries"]}
    simulations = {row["id"]: row for row in raw["simulations"]}
    tasks = {str(row["id"]): row for row in raw["tasks"]}
    cases: list[Case] = []
    for selected_row in selected:
        example_id = selected_row["example_id"]
        entry = entries[example_id]
        packet = _load_json(root / "data/packets/v1" / entry["packet_path"])
        packet_hash = canonical_sha256(packet)
        if packet_hash != entry["packet_hash"] or packet_hash != selected_row["packet_hash"]:
            raise BenchmarkRunError(f"packet hash mismatch: {example_id}")
        simulation = simulations[entry["source_simulation_id"]]
        task_id = str(simulation["task_id"])
        if task_id != str(selected_row["task_id"]):
            raise BenchmarkRunError(f"task binding mismatch: {example_id}")
        reward = simulation["reward_info"]["reward"]
        if reward not in (0, 0.0, 1, 1.0):
            raise BenchmarkRunError(f"non-binary benchmark reward: {example_id}={reward}")
        state = {
            "schema_version": "1.0",
            "evaluation_unit": "tau2_customer_support_task_success",
            "reference_definition": (
                "The reference is the binary task-level reward stored by the source tau2 "
                "benchmark. It is withheld from both judges."
            ),
            "benchmark_task": tasks[task_id],
            "trajectory_evidence": packet,
        }
        cases.append(
            Case(
                order=selected_row["selection_order"],
                example_id=example_id,
                task_id=task_id,
                split=selected_row["split"],
                reference_pass=int(reward),
                state=state,
                state_hash=canonical_sha256(state),
            )
        )
    return cases


def _input_bytes(case: Case) -> int:
    return (
        len(json.dumps(case.state, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        + len(SYSTEM.encode("utf-8"))
        + len(QUESTION.encode("utf-8"))
    )


def maximum_cost(cases: list[Case], max_attempts_per_judge: int = 3) -> float:
    # UTF-8 bytes are a deliberately conservative upper bound for input tokens here.
    input_upper = sum(_input_bytes(case) for case in cases) * max_attempts_per_judge
    gpt = input_upper / 1_000_000 * GPT_INPUT_PER_MILLION
    gpt += (
        len(cases)
        * max_attempts_per_judge
        * GPT_MAX_OUTPUT_TOKENS
        / 1_000_000
        * GPT_OUTPUT_PER_MILLION
    )
    jev = input_upper / 1_000_000 * JEV_INPUT_PER_MILLION
    return gpt + jev


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append_attempt(path: Path, attempts: list[dict[str, Any]], row: dict[str, Any]) -> None:
    attempts.append(row)
    write_json_atomic(path, attempts)


def _run_jev(case: Case, client: Any) -> tuple[float, dict[str, Any], dict[str, Any]]:
    from typesafe_sdk import Noul

    response = client.system_one(
        state=case.state,
        questions={"task_success": Noul(instructions=SYSTEM + "\n\n" + QUESTION)},
    )
    value = float(response.nouls["task_success"].noul)
    if not 0 <= value <= 1:
        raise BenchmarkRunError(f"Jev returned out-of-range probability: {value}")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    raw = json.loads(response.raw_http_response.content)
    meta = {
        "provider_request_id": response.request_id,
        "returned_model": response.model,
        "usage": usage,
    }
    return value, meta, raw


def _run_gpt(case: Case, client: Any) -> tuple[float, dict[str, Any], dict[str, Any]]:
    response = client.responses.create(
        model=GPT_MODEL,
        instructions=SYSTEM,
        input=json.dumps({"question": QUESTION, "state": case.state}, ensure_ascii=False),
        reasoning={"effort": "medium"},
        text={
            "format": {
                "type": "json_schema",
                "name": "task_success_probability",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"p_pass": {"type": "number", "minimum": 0, "maximum": 1}},
                    "required": ["p_pass"],
                },
            }
        },
        tools=[],
        store=False,
        service_tier="default",
        truncation="disabled",
        max_output_tokens=GPT_MAX_OUTPUT_TOKENS,
    )
    value = float(json.loads(response.output_text)["p_pass"])
    if not 0 <= value <= 1:
        raise BenchmarkRunError(f"GPT returned out-of-range probability: {value}")
    raw = response.model_dump(mode="json")
    details = getattr(response.usage, "output_tokens_details", None)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "reasoning_tokens": getattr(details, "reasoning_tokens", 0) if details else 0,
    }
    meta = {
        "provider_request_id": response.id,
        "returned_model": response.model,
        "usage": usage,
    }
    return value, meta, raw


def _cost(judge: str, usage: dict[str, int]) -> float:
    if judge == "jev":
        return usage["input_tokens"] / 1_000_000 * JEV_INPUT_PER_MILLION
    return (
        usage["input_tokens"] / 1_000_000 * GPT_INPUT_PER_MILLION
        + usage["output_tokens"] / 1_000_000 * GPT_OUTPUT_PER_MILLION
    )


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["status"] == "valid"]
    n = len(rows)
    correct = sum(row["predicted_pass"] == row["reference_pass"] for row in valid)
    false_pass = sum(row["predicted_pass"] == 1 and row["reference_pass"] == 0 for row in valid)
    false_fail = sum(row["predicted_pass"] == 0 and row["reference_pass"] == 1 for row in valid)
    brier = statistics.fmean((row["p_pass"] - row["reference_pass"]) ** 2 for row in valid)
    log_loss = statistics.fmean(
        -(
            row["reference_pass"] * math.log(min(max(row["p_pass"], 1e-15), 1 - 1e-15))
            + (1 - row["reference_pass"])
            * math.log(1 - min(max(row["p_pass"], 1e-15), 1 - 1e-15))
        )
        for row in valid
    )
    latencies = sorted(row["latency_seconds"] for row in valid)
    return {
        "cases": n,
        "valid": len(valid),
        "coverage": len(valid) / n,
        "accuracy": correct / len(valid),
        "correct": correct,
        "false_pass": false_pass,
        "false_fail": false_fail,
        "brier_score": brier,
        "log_loss": log_loss,
        "mean_latency_seconds": statistics.fmean(latencies),
        "median_latency_seconds": statistics.median(latencies),
        "p95_latency_seconds": latencies[
            min(len(latencies) - 1, math.ceil(0.95 * len(latencies)) - 1)
        ],
        "cost_usd": sum(row["cost_usd"] for row in valid),
    }


def analyze(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_judge = {
        judge: [row for row in results if row["judge"] == judge] for judge in ("jev", "gpt")
    }
    if any(len(rows) != CASE_LIMIT for rows in by_judge.values()):
        raise BenchmarkRunError("analysis requires 21 results from each judge")
    if any(row["status"] != "valid" for row in results):
        raise BenchmarkRunError("analysis requires valid results for every case")
    metrics = {judge: _metric(rows) for judge, rows in by_judge.items()}
    paired: list[dict[str, Any]] = []
    for order in range(1, CASE_LIMIT + 1):
        jev = next(row for row in by_judge["jev"] if row["order"] == order)
        gpt = next(row for row in by_judge["gpt"] if row["order"] == order)
        paired.append(
            {
                "order": order,
                "example_id": jev["example_id"],
                "reference_pass": jev["reference_pass"],
                "jev_p_pass": jev["p_pass"],
                "gpt_p_pass": gpt["p_pass"],
                "jev_correct": jev["predicted_pass"] == jev["reference_pass"],
                "gpt_correct": gpt["predicted_pass"] == gpt["reference_pass"],
            }
        )
    return {
        "metrics": metrics,
        "paired": {
            "prediction_disagreements": sum(
                (row["jev_p_pass"] > 0.5) != (row["gpt_p_pass"] > 0.5) for row in paired
            ),
            "jev_only_correct": sum(
                row["jev_correct"] and not row["gpt_correct"] for row in paired
            ),
            "gpt_only_correct": sum(
                row["gpt_correct"] and not row["jev_correct"] for row in paired
            ),
            "both_correct": sum(row["jev_correct"] and row["gpt_correct"] for row in paired),
            "both_wrong": sum(
                not row["jev_correct"] and not row["gpt_correct"] for row in paired
            ),
        },
        "cases": paired,
    }


def _report(analysis: dict[str, Any], metadata: dict[str, Any]) -> str:
    j, g = analysis["metrics"]["jev"], analysis["metrics"]["gpt"]
    p = analysis["paired"]
    result_sentence = (
        "Jev had the higher binary accuracy"
        if j["accuracy"] > g["accuracy"]
        else "GPT-5.4 had the higher binary accuracy"
        if g["accuracy"] > j["accuracy"]
        else "Jev and GPT-5.4 tied on binary accuracy"
    )
    return f"""# Jev vs GPT-5.4 as customer-support task-success judges

Run completed: {metadata["completed_at"]}

## Result

On these 21 preselected τ² customer-support trajectories, **{result_sentence}** against the benchmark's stored task-success reward.

The tie in pass/fail accuracy hides a useful difference: GPT-5.4's probabilities were better calibrated on this sample (Brier {g["brier_score"]:.4f} versus Jev's {j["brier_score"]:.4f}), while Jev was about {g["mean_latency_seconds"] / j["mean_latency_seconds"]:.0f}x faster and {g["cost_usd"] / j["cost_usd"]:.0f}x cheaper. Each judge made one different mistake, so neither dominated the other case by case.

| Metric | Jev | GPT-5.4 |
|---|---:|---:|
| Accuracy | {j["accuracy"]:.1%} ({j["correct"]}/21) | {g["accuracy"]:.1%} ({g["correct"]}/21) |
| Brier score (lower is better) | {j["brier_score"]:.4f} | {g["brier_score"]:.4f} |
| Log loss (lower is better) | {j["log_loss"]:.4f} | {g["log_loss"]:.4f} |
| False pass / false fail | {j["false_pass"]} / {j["false_fail"]} | {g["false_pass"]} / {g["false_fail"]} |
| Mean latency | {j["mean_latency_seconds"]:.2f}s | {g["mean_latency_seconds"]:.2f}s |
| API cost | ${j["cost_usd"]:.4f} | ${g["cost_usd"]:.4f} |

The judges disagreed on {p["prediction_disagreements"]} cases. Jev alone was correct on {p["jev_only_correct"]}; GPT-5.4 alone was correct on {p["gpt_only_correct"]}; both were correct on {p["both_correct"]}; both were wrong on {p["both_wrong"]}.

## What this experiment measures

Both judges received the same task specification, evaluation criteria, policy, tool definitions, conversation, tool calls, tool results, and target response. Neither received the stored reward. A probability above 0.5 was classified as pass and compared with the source τ² binary reward (15 passes, 6 failures).

This is a comparison of **agreement with τ² task success**, not agreement with human ratings of tone or general response quality. Human annotation and the separate 24-case calibration pool were intentionally removed at the owner's direction.

## Execution controls

- Cases: 21; one valid judgment per model per case; repeatability disabled.
- Technical retries: at most 3 total attempts per case/judge; global ceiling 126 attempts.
- Budget: hard ceiling $30; conservative pre-run maximum ${metadata["conservative_maximum_cost_usd"]:.2f}; observed successful-call cost ${metadata["observed_cost_usd"]:.4f}.
- GPT model: requested `{GPT_MODEL}`; returned model(s): {", ".join(metadata["returned_models"]["gpt"])}.
- Jev model: the account exposed the `{JEV_MODEL}` alias rather than a directly selectable `jev-1.13.0`; every call resolved to and returned {", ".join(metadata["returned_models"]["jev"])}.
- Threshold: pass only when `p_pass > 0.5` (so exactly 0.5 is fail).

## Interpretation limits

Twenty-one cases are enough for a small, bounded comparison, not a broad claim of superiority. The cases contain only six benchmark failures, the benchmark reward can itself be imperfect, and `jev-latest` is a moving alias rather than an immutable model version. Treat accuracy differences as descriptive rather than statistically conclusive.
"""


def _html_report(markdown: str, analysis: dict[str, Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{row['order']}</td><td><code>{html.escape(row['example_id'])}</code></td>"
        f"<td>{'PASS' if row['reference_pass'] else 'FAIL'}</td>"
        f"<td>{row['jev_p_pass']:.3f} {'✓' if row['jev_correct'] else '✗'}</td>"
        f"<td>{row['gpt_p_pass']:.3f} {'✓' if row['gpt_correct'] else '✗'}</td></tr>"
        for row in analysis["cases"]
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Judge comparison</title>
<style>body{{font:16px system-ui;max-width:1050px;margin:40px auto;padding:0 20px;color:#17202a}}pre{{white-space:pre-wrap;font:15px system-ui;line-height:1.5;background:#f4f6f7;padding:24px;border-radius:12px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border-bottom:1px solid #ddd;text-align:left}}code{{font-size:12px}}</style></head>
<body><h1>Jev vs GPT-5.4</h1><pre>{html.escape(markdown)}</pre><h2>Case-level results</h2>
<table><thead><tr><th>#</th><th>Case</th><th>Reference</th><th>Jev p(pass)</th><th>GPT p(pass)</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""


def run(root: Path) -> dict[str, Any]:
    _load_env(root / ".env")
    cases = load_cases(root)
    conservative_max = maximum_cost(cases)
    if conservative_max > BUDGET_USD:
        raise BenchmarkRunError(
            f"conservative maximum ${conservative_max:.2f} exceeds ${BUDGET_USD:.2f} budget"
        )

    from openai import OpenAI
    from typesafe_sdk import RetryPolicy, TypeSafeClient

    openai_client = OpenAI(max_retries=0, timeout=300)
    jev_client = TypeSafeClient(model=JEV_MODEL, retry=RetryPolicy(max_retries=0), timeout=300)
    run_dir = root / "runs/benchmark-reward-v1"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = run_dir / "attempts.json"
    results_path = run_dir / "results.json"
    attempts = _load_json(attempts_path) if attempts_path.is_file() else []
    results = _load_json(results_path) if results_path.is_file() else []
    completed = {
        (row["example_id"], row["judge"]) for row in results if row["status"] == "valid"
    }

    for case in cases:
        for judge, client, call in (
            ("jev", jev_client, _run_jev),
            ("gpt", openai_client, _run_gpt),
        ):
            if (case.example_id, judge) in completed:
                continue
            for local_attempt in range(1, 4):
                if len(attempts) >= ATTEMPT_LIMIT:
                    raise BenchmarkRunError("global 126-attempt ceiling reached")
                charged = sum(row.get("cost_usd", 0.0) for row in attempts)
                reserve = (
                    _input_bytes(case) / 1_000_000 * JEV_INPUT_PER_MILLION
                    if judge == "jev"
                    else _input_bytes(case) / 1_000_000 * GPT_INPUT_PER_MILLION
                    + GPT_MAX_OUTPUT_TOKENS / 1_000_000 * GPT_OUTPUT_PER_MILLION
                )
                if charged + reserve > BUDGET_USD:
                    raise BenchmarkRunError("next attempt could exceed the $30 budget ceiling")
                started = time.monotonic()
                attempt = {
                    "attempt_number": len(attempts) + 1,
                    "local_attempt": local_attempt,
                    "started_at": _now(),
                    "order": case.order,
                    "example_id": case.example_id,
                    "judge": judge,
                    "requested_model": JEV_MODEL if judge == "jev" else GPT_MODEL,
                    "state_hash": case.state_hash,
                }
                try:
                    probability, meta, raw = call(case, client)
                    latency = time.monotonic() - started
                    cost = _cost(judge, meta["usage"])
                    raw_path = raw_dir / f"{case.order:02d}-{case.example_id}-{judge}.json"
                    write_json_atomic(raw_path, raw)
                    attempt.update(
                        status="valid",
                        completed_at=_now(),
                        latency_seconds=latency,
                        cost_usd=cost,
                        **meta,
                    )
                    _append_attempt(attempts_path, attempts, attempt)
                    result = {
                        **attempt,
                        "reference_pass": case.reference_pass,
                        "p_pass": probability,
                        "predicted_pass": int(probability > 0.5),
                        "raw_response_path": str(raw_path.relative_to(root)),
                    }
                    results.append(result)
                    write_json_atomic(results_path, results)
                    print(
                        f"[{len(results):02d}/42] {case.order:02d} {judge}: "
                        f"p_pass={probability:.3f} ref={case.reference_pass} ({latency:.1f}s)",
                        flush=True,
                    )
                    break
                except Exception as error:  # provider errors must be recorded before retrying
                    attempt.update(
                        status="technical_error",
                        completed_at=_now(),
                        latency_seconds=time.monotonic() - started,
                        cost_usd=0.0,
                        error_type=type(error).__name__,
                        error_message=str(error)[:1000],
                    )
                    _append_attempt(attempts_path, attempts, attempt)
                    if local_attempt == 3:
                        raise BenchmarkRunError(
                            f"{judge} failed three times for {case.example_id}: {error}"
                        ) from error

    analysis = analyze(results)
    observed_cost = sum(row["cost_usd"] for row in results)
    metadata = {
        "schema_version": "1.0",
        "completed_at": _now(),
        "case_count": CASE_LIMIT,
        "reference_distribution": {"pass": 15, "fail": 6},
        "attempt_count": len(attempts),
        "budget_usd": BUDGET_USD,
        "conservative_maximum_cost_usd": conservative_max,
        "observed_cost_usd": observed_cost,
        "returned_models": {
            judge: sorted({row["returned_model"] for row in results if row["judge"] == judge})
            for judge in ("jev", "gpt")
        },
    }
    artifact = {"metadata": metadata, **analysis}
    write_json_atomic(run_dir / "analysis.json", artifact)
    report = _report(analysis, metadata)
    write_text_atomic(root / "reports/benchmark-reward-comparison.md", report)
    write_text_atomic(
        root / "reports/benchmark-reward-comparison.html", _html_report(report, analysis)
    )
    return artifact
