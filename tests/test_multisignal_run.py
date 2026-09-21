from __future__ import annotations

from pathlib import Path

import pytest

from judge_compare.benchmark_reward_run import BenchmarkRunError
from judge_compare.multisignal_run import QUESTIONS, _schema, analyze, projected_cost, run


def test_schema_and_question_names_are_identical() -> None:
    schema = _schema()
    assert list(schema["properties"]) == list(QUESTIONS)
    assert schema["required"] == list(QUESTIONS)
    assert schema["additionalProperties"] is False


def test_hundred_repetition_projection_uses_measured_baseline() -> None:
    projection = projected_cost(100)
    assert projection["prior_run_usage_projection_usd"] == pytest.approx(83.1813318)
    assert projection["prior_run_usage_projection_with_20_percent_buffer_usd"] == pytest.approx(
        99.81759816
    )


def test_run_rejects_insufficient_budget_before_loading_credentials(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkRunError, match="no provider calls were made"):
        run(tmp_path, repetitions=100, budget_usd=30)


def test_repeated_analysis_measures_accuracy_and_stability() -> None:
    rows = []
    for judge in ("jev", "gpt"):
        for order in range(1, 22):
            reference = int(order % 2 == 0)
            rows.append(
                {
                    "judge": judge,
                    "status": "valid",
                    "order": order,
                    "example_id": f"case-{order}",
                    "reference_pass": reference,
                    "probabilities": {
                        signal: (0.9 if reference else 0.1) for signal in QUESTIONS
                    },
                    "latency_seconds": 1.0,
                    "cost_usd": 0.01,
                }
            )
    result = analyze(rows, repetitions=1)
    assert result["metrics"]["jev"]["accuracy"] == 1.0
    assert result["metrics"]["gpt"]["binary_repeatability"] == 1.0
    assert result["metrics"]["jev"]["task_success_mean_per_case_variance"] == 0.0
