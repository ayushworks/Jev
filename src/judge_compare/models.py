from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

Criterion = Literal["grounding", "relevance", "policy_compliance"]
Decision = Literal["PASS", "FAIL"]
TerminalStatus = Literal[
    "ok",
    "timeout",
    "rate_limited",
    "invalid_output",
    "provider_error",
    "budget_blocked",
]
StudyTrack = Literal["development", "primary", "repeatability"]


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankText = Annotated[str, Field(min_length=1), AfterValidator(_nonblank)]
Sha256Text = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FORBIDDEN_PACKET_KEYS = {
    "reward",
    "reward_info",
    "human_label",
    "human_labels",
    "labels",
    "reference_outputs",
    "expected_actions",
    "evaluation_criteria",
    "evaluator_output",
    "judge_output",
    "raw_data",
    "usage",
    "cost",
    "agent_model",
    "source_model",
    "user_scenario",
    "future_messages",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToolDefinition(StrictModel):
    name: NonBlankText
    description: NonBlankText
    parameters: dict[str, JsonValue]


_SCALAR_PARAMETER_TYPES = frozenset({"string", "integer", "number", "boolean"})


def _validate_parameter_value(value: JsonValue, schema: dict[str, JsonValue], *, context: str) -> None:
    """Validate one value against the packet builder's closed JSON Schema subset."""
    expected = schema["type"]
    if expected == "string":
        matches = isinstance(value, str)
    elif expected == "integer":
        matches = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        matches = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "boolean":
        matches = isinstance(value, bool)
    elif expected == "array":
        matches = isinstance(value, list)
    else:  # The schema is checked before values are checked.
        raise ValueError(f"{context} uses unsupported JSON Schema type {expected!r}")

    if not matches:
        raise ValueError(f"{context} does not match JSON Schema type {expected!r}")
    if expected == "number":
        try:
            finite = math.isfinite(value)  # type: ignore[arg-type]
        except (OverflowError, TypeError):
            finite = False
        if not finite:
            raise ValueError(f"{context} must be a finite JSON number")
    if expected == "array":
        item_schema = schema["items"]
        if not isinstance(item_schema, dict):  # Kept local so validation fails closed.
            raise ValueError(f"{context} has an invalid array item schema")
        for index, item in enumerate(value):
            _validate_parameter_value(item, item_schema, context=f"{context}[{index}]")


def _validate_parameter_schema(
    schema: JsonValue,
    *,
    context: str,
    require_description: bool,
) -> dict[str, JsonValue]:
    """Validate a schema emitted by ``packet_builder._json_schema_for_annotation``."""
    if not isinstance(schema, dict):
        raise ValueError(f"{context} must be a JSON Schema object")
    schema_type = schema.get("type")
    if not isinstance(schema_type, str) or schema_type not in {
        *_SCALAR_PARAMETER_TYPES,
        "array",
    }:
        raise ValueError(f"{context} uses unsupported JSON Schema type {schema_type!r}")

    expected_keys = {"type"}
    if schema_type == "array":
        expected_keys.add("items")
    if require_description:
        expected_keys.update({"description", "title"})
    missing = expected_keys - set(schema)
    unsupported = set(schema) - expected_keys
    if missing or unsupported:
        raise ValueError(
            f"{context} has unsupported JSON Schema shape: "
            f"missing={sorted(missing)}, unsupported={sorted(unsupported)}"
        )

    if require_description:
        description = schema["description"]
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"{context}.description must be a nonblank string")
        title = schema["title"]
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"{context}.title must be a nonblank string")
    if schema_type == "array":
        _validate_parameter_schema(
            schema["items"],
            context=f"{context}.items",
            require_description=False,
        )
    return schema


def _validated_parameter_properties(
    definition: ToolDefinition,
) -> tuple[dict[str, dict[str, JsonValue]], set[str]]:
    parameters = definition.parameters
    expected_keys = {"type", "properties", "title"}
    if "required" in parameters:
        expected_keys.add("required")
    missing = expected_keys - set(parameters)
    unsupported = set(parameters) - expected_keys
    if missing or unsupported:
        raise ValueError(
            f"tool {definition.name!r} has unsupported parameters schema: "
            f"missing={sorted(missing)}, unsupported={sorted(unsupported)}"
        )
    if parameters["type"] != "object":
        raise ValueError(f"tool {definition.name!r} parameters.type must equal 'object'")
    if parameters["title"] != "parameters":
        raise ValueError(f"tool {definition.name!r} parameters.title must equal 'parameters'")

    raw_properties = parameters["properties"]
    raw_required = parameters.get("required", [])
    if not isinstance(raw_properties, dict):
        raise ValueError(f"tool {definition.name!r} parameters.properties must be an object")
    if not isinstance(raw_required, list):
        raise ValueError(f"tool {definition.name!r} parameters.required must be an array")

    properties: dict[str, dict[str, JsonValue]] = {}
    for property_name, schema in raw_properties.items():
        if not property_name.strip():
            raise ValueError(f"tool {definition.name!r} has a blank parameter name")
        properties[property_name] = _validate_parameter_schema(
            schema,
            context=f"tool {definition.name!r} parameter {property_name!r}",
            require_description=True,
        )

    required: list[str] = []
    for item in raw_required:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"tool {definition.name!r} parameters.required must contain nonblank strings"
            )
        required.append(item)
    if len(required) != len(set(required)):
        raise ValueError(f"tool {definition.name!r} parameters.required must be unique")
    unknown_required = set(required) - set(properties)
    if unknown_required:
        raise ValueError(
            f"tool {definition.name!r} requires undefined parameters: "
            f"{sorted(unknown_required)}"
        )
    return properties, set(required)


class ToolCallEvidence(StrictModel):
    id: NonBlankText
    name: NonBlankText
    arguments: dict[str, JsonValue]
    requestor: Literal["assistant"] = "assistant"


class UserEvidence(StrictModel):
    role: Literal["user"]
    content: NonBlankText
    turn_idx: int = Field(ge=0)
    source_message_id: NonBlankText | None = None


class AssistantEvidence(StrictModel):
    role: Literal["assistant"]
    content: NonBlankText | None
    tool_calls: list[ToolCallEvidence] | None
    turn_idx: int = Field(ge=0)
    source_message_id: NonBlankText | None = None


class ToolEvidence(StrictModel):
    role: Literal["tool"]
    id: NonBlankText
    content: NonBlankText
    requestor: Literal["assistant"] = "assistant"
    error: bool
    turn_idx: int = Field(ge=0)
    source_message_id: NonBlankText | None = None


ConversationEvidence = Annotated[
    UserEvidence | AssistantEvidence | ToolEvidence,
    Field(discriminator="role"),
]


class TargetResponse(StrictModel):
    role: Literal["assistant"] = "assistant"
    content: NonBlankText
    turn_idx: int = Field(ge=0)
    source_message_id: NonBlankText | None = None


class EvidencePacket(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    example_id: NonBlankText
    policy: NonBlankText
    tool_definitions: list[ToolDefinition] = Field(min_length=1)
    conversation_prefix: list[ConversationEvidence] = Field(min_length=1)
    target_response: TargetResponse

    @model_validator(mode="after")
    def validate_evidence_contract(self) -> EvidencePacket:
        forbidden = _find_forbidden_keys(self.model_dump(mode="json"))
        if forbidden:
            raise ValueError(f"packet contains forbidden keys: {', '.join(sorted(forbidden))}")

        turns = [message.turn_idx for message in self.conversation_prefix]
        expected_turns = list(range(len(self.conversation_prefix)))
        if turns != expected_turns:
            raise ValueError("conversation turn_idx values must be contiguous from zero")
        if self.target_response.turn_idx != turns[-1] + 1:
            raise ValueError("target_response turn_idx must be exactly one after the prefix")

        assistant_messages = (
            message
            for message in self.conversation_prefix
            if isinstance(message, AssistantEvidence)
        )
        if any(message.content is None and not message.tool_calls for message in assistant_messages):
            raise ValueError("assistant evidence must contain content or tool_calls")

        source_message_ids = [
            message.source_message_id
            for message in [*self.conversation_prefix, self.target_response]
            if message.source_message_id is not None
        ]
        if len(source_message_ids) != len(set(source_message_ids)):
            raise ValueError("non-null source_message_id values must be unique")

        definition_names = [definition.name for definition in self.tool_definitions]
        if len(definition_names) != len(set(definition_names)):
            raise ValueError("tool definition names must be unique")
        definitions: dict[str, tuple[dict[str, dict[str, JsonValue]], set[str]]] = {
            definition.name: _validated_parameter_properties(definition)
            for definition in self.tool_definitions
        }

        call_turns = {
            call.id: message.turn_idx
            for message in self.conversation_prefix
            if isinstance(message, AssistantEvidence)
            for call in (message.tool_calls or [])
        }
        result_turns = {
            message.id: message.turn_idx
            for message in self.conversation_prefix
            if isinstance(message, ToolEvidence)
        }
        call_ids = [
            call.id
            for message in self.conversation_prefix
            if isinstance(message, AssistantEvidence)
            for call in (message.tool_calls or [])
        ]
        result_ids = [
            message.id
            for message in self.conversation_prefix
            if isinstance(message, ToolEvidence)
        ]
        if len(call_ids) != len(call_turns) or len(result_ids) != len(result_turns):
            raise ValueError("tool call/result IDs must be unique")
        if set(call_ids) != set(result_ids):
            raise ValueError("every tool call must have exactly one paired result")
        if any(result_turns[call_id] <= call_turns[call_id] for call_id in call_ids):
            raise ValueError("every tool result must occur after its paired call")

        for message in self.conversation_prefix:
            if not isinstance(message, AssistantEvidence):
                continue
            for call in message.tool_calls or []:
                definition = definitions.get(call.name)
                if definition is None:
                    raise ValueError(f"tool call {call.id!r} uses undefined tool {call.name!r}")
                properties, required = definition
                missing = required - set(call.arguments)
                unexpected = set(call.arguments) - set(properties)
                if missing or unexpected:
                    raise ValueError(
                        f"tool call {call.id!r} arguments mismatch for {call.name!r}: "
                        f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
                    )
                for argument_name, argument in call.arguments.items():
                    _validate_parameter_value(
                        argument,
                        properties[argument_name],
                        context=f"tool call {call.id!r}.arguments.{argument_name}",
                    )

        return self


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_PACKET_KEYS:
                found.add(key)
            found.update(_find_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_find_forbidden_keys(child))
    return found


class CriterionResult(StrictModel):
    p_pass: float = Field(ge=0, le=1)
    p_failure: float = Field(ge=0, le=1)
    decision: Decision

    @model_validator(mode="after")
    def probabilities_and_decision_agree(self) -> CriterionResult:
        if abs(self.p_failure - (1.0 - self.p_pass)) > 1e-9:
            raise ValueError("p_failure must equal 1 - p_pass")
        expected = "FAIL" if self.p_failure >= 0.5 else "PASS"
        if self.decision != expected:
            raise ValueError(f"decision must be {expected} at the fixed 0.5 threshold")
        return self


class JudgeResult(StrictModel):
    example_id: NonBlankText
    packet_hash: Sha256Text
    rubric_hash: Sha256Text
    config_hash: Sha256Text
    study_track: StudyTrack
    repetition_id: int = Field(ge=0)
    judge: NonBlankText
    requested_model_id: NonBlankText
    returned_model_id: NonBlankText | None
    raw_response_location: NonBlankText | None
    criteria: dict[Criterion, CriterionResult]
    request_ids: list[NonBlankText]
    started_at: datetime
    ended_at: datetime
    latency_seconds: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_basis: NonBlankText | None
    attempts: int = Field(ge=0, le=3)
    terminal_status: TerminalStatus

    @model_validator(mode="after")
    def validate_track_and_status(self) -> JudgeResult:
        if self.study_track == "primary" and self.repetition_id != 0:
            raise ValueError("primary results must use repetition_id=0")
        if self.study_track == "repeatability" and self.repetition_id == 0:
            raise ValueError("additional repeatability results must use repetition_id>=1")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        if self.started_at.utcoffset() is None or self.ended_at.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        if len(self.request_ids) != len(set(self.request_ids)):
            raise ValueError("request IDs must be unique")
        if self.terminal_status == "budget_blocked" and self.attempts != 0:
            raise ValueError("budget_blocked results must have zero attempts")
        if self.terminal_status != "budget_blocked" and self.attempts < 1:
            raise ValueError("attempted results must have at least one attempt")
        if self.terminal_status == "ok":
            expected = {"grounding", "relevance", "policy_compliance"}
            if set(self.criteria) != expected:
                raise ValueError("ok results must contain all three criteria")
            if not self.request_ids:
                raise ValueError("ok results must record at least one request ID")
            if not self.returned_model_id:
                raise ValueError("ok results must record the returned model ID")
            if not self.raw_response_location:
                raise ValueError("ok results must preserve a raw response location")
            if self.input_tokens is None or self.output_tokens is None:
                raise ValueError("ok results must record input and output token counts")
            if self.cost_usd is None or not self.cost_basis:
                raise ValueError("ok results must record cost and its basis")
        return self
