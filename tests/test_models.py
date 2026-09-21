from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from judge_compare.models import CriterionResult, EvidencePacket, JudgeResult


def test_exact_half_failure_probability_maps_to_fail() -> None:
    result = CriterionResult(p_pass=0.5, p_failure=0.5, decision="FAIL")

    assert result.decision == "FAIL"


def test_probability_complement_is_validated() -> None:
    with pytest.raises(ValidationError, match="p_failure must equal"):
        CriterionResult(p_pass=0.8, p_failure=0.3, decision="PASS")


def test_primary_result_must_be_repetition_zero() -> None:
    started = datetime.now(UTC)
    criteria = {
        name: CriterionResult(p_pass=0.9, p_failure=0.1, decision="PASS")
        for name in ("grounding", "relevance", "policy_compliance")
    }
    with pytest.raises(ValidationError, match="primary results must use repetition_id=0"):
        JudgeResult(
            example_id="example-1",
            packet_hash="a" * 64,
            rubric_hash="b" * 64,
            config_hash="c" * 64,
            study_track="primary",
            repetition_id=1,
            judge="jev",
            requested_model_id="jev-1.13.0",
            returned_model_id="jev-1.13.0",
            raw_response_location="runs/raw/example-1.json",
            criteria=criteria,
            request_ids=["request-1"],
            started_at=started,
            ended_at=started + timedelta(seconds=1),
            latency_seconds=1,
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=0.001,
            cost_basis="test fixture",
            attempts=1,
            terminal_status="ok",
        )


def _packet(**overrides: object) -> dict:
    value = {
        "schema_version": "1.0",
        "example_id": "opaque-1",
        "policy": "Use only supplied evidence.",
        "tool_definitions": [
            {
                "name": "lookup",
                "description": "Look up a record.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "title": "parameters",
                },
            }
        ],
        "conversation_prefix": [
            {"role": "user", "content": "Please check.", "turn_idx": 0},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "lookup",
                        "arguments": {},
                        "requestor": "assistant",
                    }
                ],
                "turn_idx": 1,
            },
            {
                "role": "tool",
                "id": "call-1",
                "content": "found",
                "requestor": "assistant",
                "error": False,
                "turn_idx": 2,
            },
        ],
        "target_response": {
            "role": "assistant",
            "content": "I found it.",
            "turn_idx": 3,
        },
    }
    value.update(overrides)
    return value


def test_packet_contract_accepts_only_paired_allowlisted_evidence() -> None:
    packet = EvidencePacket.model_validate(_packet())

    assert packet.target_response.content == "I found it."


def test_packet_rejects_top_level_reward_leakage() -> None:
    value = _packet()
    value["reward"] = 1

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_nested_label_leakage() -> None:
    value = _packet()
    value["tool_definitions"][0]["parameters"]["human_label"] = "PASS"

    with pytest.raises(ValidationError, match="forbidden keys"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_unmatched_tool_result() -> None:
    value = _packet()
    value["conversation_prefix"][2]["id"] = "wrong-id"

    with pytest.raises(ValidationError, match="paired result"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_tool_result_before_call() -> None:
    value = _packet()
    value["conversation_prefix"][1]["turn_idx"] = 2
    value["conversation_prefix"][2]["turn_idx"] = 1
    value["conversation_prefix"][1], value["conversation_prefix"][2] = (
        value["conversation_prefix"][2],
        value["conversation_prefix"][1],
    )

    with pytest.raises(ValidationError, match="after its paired call"):
        EvidencePacket.model_validate(value)


@pytest.mark.parametrize("tool_calls", [None, []])
def test_packet_rejects_empty_assistant_evidence(tool_calls: object) -> None:
    value = _packet()
    value["conversation_prefix"][1]["tool_calls"] = tool_calls

    with pytest.raises(ValidationError, match="assistant evidence must contain"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_duplicate_tool_definition_names() -> None:
    value = _packet()
    value["tool_definitions"].append(deepcopy(value["tool_definitions"][0]))

    with pytest.raises(ValidationError, match="tool definition names must be unique"):
        EvidencePacket.model_validate(value)


def test_packet_requires_contiguous_turns_from_zero() -> None:
    value = _packet()
    value["conversation_prefix"][1]["turn_idx"] = 2
    value["conversation_prefix"][2]["turn_idx"] = 3
    value["target_response"]["turn_idx"] = 4

    with pytest.raises(ValidationError, match="contiguous from zero"):
        EvidencePacket.model_validate(value)


def test_packet_requires_target_immediately_after_prefix() -> None:
    value = _packet()
    value["target_response"]["turn_idx"] = 4

    with pytest.raises(ValidationError, match="exactly one after the prefix"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_duplicate_non_null_source_message_ids() -> None:
    value = _packet()
    value["conversation_prefix"][0]["source_message_id"] = "msg-1"
    value["target_response"]["source_message_id"] = "msg-1"

    with pytest.raises(ValidationError, match="source_message_id values must be unique"):
        EvidencePacket.model_validate(value)


def _set_lookup_parameters(value: dict, schema: dict, argument: object) -> None:
    value["tool_definitions"][0]["parameters"] = {
        "type": "object",
        "properties": {"value": schema},
        "required": ["value"],
        "title": "parameters",
    }
    value["conversation_prefix"][1]["tool_calls"][0]["arguments"] = {"value": argument}


def test_packet_validates_array_arguments_recursively() -> None:
    value = _packet()
    _set_lookup_parameters(
        value,
        {
            "type": "array",
            "items": {"type": "array", "items": {"type": "integer"}},
            "description": "Nested integer groups.",
            "title": "Value",
        },
        [[1, 2], [3]],
    )

    packet = EvidencePacket.model_validate(value)

    assert packet.conversation_prefix[1].tool_calls[0].arguments == {
        "value": [[1, 2], [3]]
    }


@pytest.mark.parametrize("argument", [[[1, True]], [[1, "2"]]])
def test_packet_rejects_wrong_nested_array_item_types(argument: object) -> None:
    value = _packet()
    _set_lookup_parameters(
        value,
        {
            "type": "array",
            "items": {"type": "array", "items": {"type": "integer"}},
            "description": "Nested integer groups.",
            "title": "Value",
        },
        argument,
    )

    with pytest.raises(ValidationError, match="does not match JSON Schema type 'integer'"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_undefined_tool_name() -> None:
    value = _packet()
    value["conversation_prefix"][1]["tool_calls"][0]["name"] = "missing"

    with pytest.raises(ValidationError, match="uses undefined tool 'missing'"):
        EvidencePacket.model_validate(value)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({}, "missing=\\['value'\\]"),
        ({"value": "ok", "extra": 1}, "unexpected=\\['extra'\\]"),
    ],
)
def test_packet_rejects_missing_or_unexpected_arguments(
    arguments: dict, expected: str
) -> None:
    value = _packet()
    _set_lookup_parameters(
        value,
        {"type": "string", "description": "A lookup value.", "title": "Value"},
        "ok",
    )
    value["conversation_prefix"][1]["tool_calls"][0]["arguments"] = arguments

    with pytest.raises(ValidationError, match=expected):
        EvidencePacket.model_validate(value)


@pytest.mark.parametrize(
    "malformed_schema",
    [
        {"type": "string", "description": "A value.", "title": "Value", "enum": ["x"]},
        {"type": "object", "description": "A value.", "title": "Value"},
        {"type": "array", "description": "Values.", "title": "Value"},
    ],
)
def test_packet_fails_closed_for_unsupported_parameter_schema(
    malformed_schema: dict,
) -> None:
    value = _packet()
    _set_lookup_parameters(value, malformed_schema, "x")

    with pytest.raises(ValidationError, match="unsupported JSON Schema"):
        EvidencePacket.model_validate(value)


def test_packet_rejects_blank_identifier() -> None:
    value = _packet(example_id="   ")

    with pytest.raises(ValidationError, match="must not be blank"):
        EvidencePacket.model_validate(value)


def test_result_rejects_non_sha256_hash() -> None:
    started = datetime.now(UTC)
    with pytest.raises(ValidationError):
        JudgeResult(
            example_id="example-1",
            packet_hash="not-a-hash",
            rubric_hash="b" * 64,
            config_hash="c" * 64,
            study_track="development",
            repetition_id=0,
            judge="jev",
            requested_model_id="jev-1.13.0",
            returned_model_id=None,
            raw_response_location=None,
            criteria={},
            request_ids=[],
            started_at=started,
            ended_at=started,
            latency_seconds=0,
            input_tokens=None,
            cached_input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            cost_basis=None,
            attempts=1,
            terminal_status="provider_error",
        )
