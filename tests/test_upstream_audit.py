from judge_compare.upstream_audit import (
    _accuracy_checks,
    _cost_latency_rows,
    _variance_ratio_checks,
)


def test_archived_accuracy_is_recomputed_from_case_aggregates() -> None:
    accuracy = {
        "judges": {
            "judge": {
                "does_pass_accuracy": 0.75,
                "predictions": 4,
                "cases": [
                    {"does_pass_accuracy": 1.0, "repetitions": 2},
                    {"does_pass_accuracy": 0.5, "repetitions": 2},
                ],
            }
        }
    }

    assert _accuracy_checks(accuracy)["judge"]["matches"] is True


def test_archived_variance_ratio_is_only_an_algebraic_check() -> None:
    benchmark = {
        "analysis": {
            "comparison": {
                "judge": {
                    "quality": {
                        "jev_mean_variance": 0.25,
                        "judge_mean_variance": 0.5,
                        "judge_to_jev_variance_ratio": 2.0,
                    }
                }
            }
        }
    }

    assert _variance_ratio_checks(benchmark)["judge"]["matches"] is True


def test_cost_table_parser_preserves_quoted_units() -> None:
    readme = """### Cost

| Judge | Average cost/call | Average latency | Total evaluator cost |
| --- | ---: | ---: | ---: |
| Jev | $0.00035 | 0.44 s | $0.34 |

## Next
"""

    rows = _cost_latency_rows(readme)

    assert rows == [
        {
            "judge": "Jev",
            "average_cost_per_call_usd": "0.00035",
            "average_latency_seconds": "0.44",
            "total_evaluator_cost_usd": "0.34",
        }
    ]
