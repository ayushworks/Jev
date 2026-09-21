from judge_compare.source_audit import (
    _extra_paths,
    _last_assistant_text,
    _project_to_reference_shape,
    _safe_sample,
    _tool_pair_counts,
)


def _fixture() -> dict:
    messages = [
        {"role": "user", "content": "help", "turn_idx": 0},
        {
            "role": "assistant",
            "content": "checking",
            "turn_idx": 1,
            "tool_calls": [{"id": "call-1", "name": "lookup", "arguments": {}}],
        },
        {"role": "tool", "id": "call-1", "content": "ok", "turn_idx": 2},
        {"role": "assistant", "content": "done", "turn_idx": 3, "tool_calls": None},
        {"role": "user", "content": "thanks ###STOP###", "turn_idx": 4},
    ]
    return {
        "simulations": [
            {
                "id": "sim-1",
                "task_id": "task-1",
                "trial": 0,
                "termination_reason": "user_stop",
                "policy": "policy",
                "messages": messages,
            }
        ]
    }


def test_last_assistant_text_precedes_terminal_user_message() -> None:
    simulation = _fixture()["simulations"][0]

    assert _last_assistant_text(simulation)["content"] == "done"


def test_source_sample_omits_post_target_and_raw_fields() -> None:
    sample = _safe_sample(_fixture())

    assert sample["messages_after_target_omitted"] == 1
    assert sample["conversation_through_target"][-1]["content"] == "done"


def test_tool_calls_and_results_are_paired_by_id() -> None:
    counts = _tool_pair_counts(_fixture()["simulations"])

    assert counts["tool_calls"] == 1
    assert counts["tool_results"] == 1
    assert counts["unmatched_tool_calls"] == 0
    assert counts["unmatched_tool_results"] == 0
    assert counts["duplicate_tool_call_ids"] == 0
    assert counts["duplicate_tool_result_ids"] == 0
    assert counts["out_of_order_tool_results"] == 0


def test_tool_pairing_preserves_duplicate_multiplicity() -> None:
    simulation = _fixture()["simulations"][0]
    simulation["messages"][1]["tool_calls"].append(
        {"id": "call-1", "name": "lookup", "arguments": {}}
    )

    counts = _tool_pair_counts([simulation])

    assert counts["unmatched_tool_calls"] == 1
    assert counts["duplicate_tool_call_ids"] == 1


def test_release_shape_comparison_does_not_hide_release_field_changes() -> None:
    artifact = {"id": 1, "nested": {"value": "same", "derived": True}}
    release = {"id": 1, "nested": {"value": "same"}}

    assert _project_to_reference_shape(artifact, release) == release
    assert _extra_paths(artifact, release) == {"task.nested.derived"}
