from __future__ import annotations

import ast
import inspect
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from judge_compare.hashing import canonical_json_bytes, canonical_sha256, sha256_file
from judge_compare.io import write_json_atomic
from judge_compare.models import EvidencePacket, ToolDefinition
from judge_compare.paths import safe_join

PACKET_BUILDER_VERSION = "1.1"
EXAMPLE_ID_NAMESPACE = "tau2-retail-response-v1"
MESSAGE_ID_NAMESPACE = "tau2-retail-message-v1"
CALL_ID_NAMESPACE = "tau2-retail-tool-call-v1"
PINNED_RETAIL_COMPATIBILITY_REVISION = "17e07b1da2bbc0cadfddeea36412686e0604127b"
PINNED_RETAIL_TOOL_DEFINITIONS_SHA256 = (
    "10637249d35710a3aece79a3cc22c033bd09c0e0692393728cb138e0b9d92a2a"
)
TERMINAL_CONTROL_RE = re.compile(r"###(STOP|TRANSFER|OUT-OF-SCOPE)###")
SECTION_HEADER_RE = re.compile(r"^[A-Za-z][A-Za-z ]*:\s*$")
ARGUMENT_DOC_RE = re.compile(r"^\s{4}([A-Za-z_]\w*):\s*(.*)$")


class PacketBuildError(ValueError):
    """Raised when source evidence cannot be exported without ambiguity."""


@dataclass(frozen=True)
class BuiltPacket:
    packet: EvidencePacket
    packet_hash: str
    manifest_entry: dict[str, Any]


@dataclass(frozen=True)
class CorpusBuild:
    packets: tuple[BuiltPacket, ...]
    manifest: dict[str, Any]


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PacketBuildError(f"could not read JSON object from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PacketBuildError(f"expected a JSON object in {path}")
    return value


def _decorated_as_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == "is_tool"
        for decorator in node.decorator_list
    )


def _json_schema_for_annotation(annotation: ast.expr | None, *, context: str) -> dict[str, Any]:
    if annotation is None:
        raise PacketBuildError(f"missing type annotation for {context}")
    if isinstance(annotation, ast.Name):
        primitive = {
            "str": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
        }.get(annotation.id)
        if primitive is not None:
            return {"type": primitive}
    if isinstance(annotation, ast.Subscript):
        container = annotation.value
        if isinstance(container, ast.Name) and container.id in {"List", "list"}:
            return {
                "type": "array",
                "items": _json_schema_for_annotation(annotation.slice, context=context),
            }
    rendered = ast.unparse(annotation) if hasattr(ast, "unparse") else type(annotation).__name__
    raise PacketBuildError(f"unsupported type annotation for {context}: {rendered}")


def _docstring_parts(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, dict[str, str]]:
    raw = ast.get_docstring(node, clean=False)
    if raw is None:
        raise PacketBuildError(f"tool {node.name} has no docstring")
    lines = inspect.cleandoc(raw).splitlines()
    section_indices = [
        index
        for index, line in enumerate(lines)
        if SECTION_HEADER_RE.match(line) and not line.startswith(" ")
    ]
    description_end = section_indices[0] if section_indices else len(lines)
    args_index = next(
        (index for index, line in enumerate(lines) if line.strip() == "Args:"),
        len(lines),
    )

    description_lines = lines[:description_end]
    while description_lines and not description_lines[-1].strip():
        description_lines.pop()
    while description_lines and not description_lines[0].strip():
        description_lines.pop(0)
    short_description = description_lines[0].strip() if description_lines else ""
    long_description_lines = description_lines[1:]
    while long_description_lines and not long_description_lines[0].strip():
        long_description_lines.pop(0)
    while long_description_lines and not long_description_lines[-1].strip():
        long_description_lines.pop()
    long_description = "\n".join(long_description_lines).strip()
    description = (
        f"{short_description}\n\n{long_description}"
        if long_description
        else short_description
    )
    if not description:
        raise PacketBuildError(f"tool {node.name} has no description")

    argument_descriptions: dict[str, str] = {}
    current: str | None = None
    if args_index < len(lines):
        for line in lines[args_index + 1 :]:
            if SECTION_HEADER_RE.match(line) and not line.startswith(" "):
                break
            match = ARGUMENT_DOC_RE.match(line)
            if match:
                current = match.group(1)
                argument_descriptions[current] = match.group(2).strip()
            elif current is not None and line.strip():
                argument_descriptions[current] = (
                    f"{argument_descriptions[current]}\n{line.strip()}".strip()
                )
    return description, argument_descriptions


def reconstruct_tool_definitions(tools_source: Path) -> list[ToolDefinition]:
    """Reconstruct reviewed JSON tool definitions without importing tau2 code."""
    try:
        tree = ast.parse(tools_source.read_text(encoding="utf-8"), filename=str(tools_source))
    except (OSError, SyntaxError) as exc:
        raise PacketBuildError(f"could not parse tool source {tools_source}: {exc}") from exc

    tool_nodes = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _decorated_as_tool(node)
        ),
        key=lambda node: node.lineno,
    )
    definitions: list[ToolDefinition] = []
    for node in tool_nodes:
        if node.args.vararg is not None or node.args.kwarg is not None:
            raise PacketBuildError(f"variadic tool signatures are unsupported: {node.name}")

        positional = [*node.args.posonlyargs, *node.args.args]
        if positional and positional[0].arg == "self":
            positional = positional[1:]
        keyword_only = list(node.args.kwonlyargs)
        arguments = [*positional, *keyword_only]

        positional_defaults = [None] * (len(positional) - len(node.args.defaults)) + list(
            node.args.defaults
        )
        keyword_defaults = list(node.args.kw_defaults)
        defaults = [*positional_defaults, *keyword_defaults]
        description, argument_descriptions = _docstring_parts(node)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for argument, default in zip(arguments, defaults, strict=True):
            schema = _json_schema_for_annotation(
                argument.annotation,
                context=f"{node.name}.{argument.arg}",
            )
            argument_description = argument_descriptions.get(argument.arg)
            if not argument_description:
                raise PacketBuildError(
                    f"tool {node.name} has no Args description for {argument.arg}"
                )
            schema["description"] = argument_description
            schema["title"] = argument.arg.replace("_", " ").title()
            properties[argument.arg] = schema
            if default is None:
                required.append(argument.arg)

        undocumented = set(argument_descriptions) - set(properties)
        if undocumented:
            raise PacketBuildError(
                f"tool {node.name} documents unknown arguments: {sorted(undocumented)}"
            )
        parameters: dict[str, Any] = {
            "properties": properties,
            "title": "parameters",
            "type": "object",
        }
        if required:
            parameters["required"] = required
        definitions.append(
            ToolDefinition(
                name=node.name,
                description=description,
                parameters=parameters,
            )
        )

    if not definitions:
        raise PacketBuildError(f"no @is_tool definitions found in {tools_source}")
    names = [definition.name for definition in definitions]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise PacketBuildError(f"duplicate tool definitions: {duplicates}")
    # The packet preserves the order exposed by the pinned toolkit. Order is
    # semantically neutral for providers, but retaining it makes the evidence
    # byte-for-byte auditable against Tool.openai_schema at the compatibility pin.
    return definitions


def _provider_message_id(message: Mapping[str, Any], *, context: str) -> str | None:
    """Read a provider ID for the private traceability manifest only."""
    raw_data = message.get("raw_data")
    if raw_data is None:
        return None
    if not isinstance(raw_data, Mapping):
        raise PacketBuildError(f"{context}.raw_data must be an object or null")
    value = raw_data.get("id")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PacketBuildError(f"{context}.raw_data.id must be a nonblank string")
    return value


def _evidence_message_id(*, simulation_id: str, turn_idx: int) -> str:
    digest = canonical_sha256(
        {
            "namespace": MESSAGE_ID_NAMESPACE,
            "simulation_id": simulation_id,
            "turn_idx": turn_idx,
        }
    )
    return f"msg-{digest[:24]}"


def _evidence_call_id(*, simulation_id: str, source_call_id: str) -> str:
    digest = canonical_sha256(
        {
            "namespace": CALL_ID_NAMESPACE,
            "simulation_id": simulation_id,
            "source_call_id": source_call_id,
        }
    )
    return f"call-{digest[:24]}"


def _nonblank_string(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PacketBuildError(f"{context} must be a nonblank string")
    return value


def _turn_index(message: Mapping[str, Any], *, context: str) -> int:
    value = message.get("turn_idx")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PacketBuildError(f"{context}.turn_idx must be a nonnegative integer")
    return value


def _validate_json_value(value: Any, schema: Mapping[str, Any], *, context: str) -> None:
    expected = schema.get("type")
    matches = {
        "string": lambda candidate: isinstance(candidate, str),
        "integer": lambda candidate: isinstance(candidate, int)
        and not isinstance(candidate, bool),
        "number": lambda candidate: isinstance(candidate, (int, float))
        and not isinstance(candidate, bool),
        "boolean": lambda candidate: isinstance(candidate, bool),
        "array": lambda candidate: isinstance(candidate, list),
    }
    predicate = matches.get(expected)
    if predicate is None or not predicate(value):
        raise PacketBuildError(f"{context} does not match JSON Schema type {expected!r}")
    if expected == "number" and not math.isfinite(value):
        raise PacketBuildError(f"{context} must be a finite JSON number")
    if expected == "array":
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise PacketBuildError(f"{context} has an invalid array item schema")
        for index, item in enumerate(value):
            _validate_json_value(item, item_schema, context=f"{context}[{index}]")


def _validate_call_arguments(
    *,
    name: str,
    arguments: Mapping[str, Any],
    definitions: Mapping[str, ToolDefinition],
    context: str,
) -> None:
    definition = definitions.get(name)
    if definition is None:
        raise PacketBuildError(f"{context} uses undefined tool {name!r}")
    parameters = definition.parameters
    properties = parameters.get("properties")
    required = parameters.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise PacketBuildError(f"tool {name!r} has malformed reconstructed parameters")
    missing = set(required) - set(arguments)
    unexpected = set(arguments) - set(properties)
    if missing or unexpected:
        raise PacketBuildError(
            f"{context} arguments mismatch for {name!r}: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )
    for key, value in arguments.items():
        schema = properties[key]
        if not isinstance(schema, Mapping):
            raise PacketBuildError(f"tool {name!r} property schema is not an object: {key}")
        _validate_json_value(value, schema, context=f"{context}.arguments.{key}")


def _export_prefix_message(
    message: Mapping[str, Any],
    *,
    simulation_id: str,
    call_id_map: Mapping[str, str],
    definitions: Mapping[str, ToolDefinition],
    context: str,
) -> dict[str, Any]:
    role = message.get("role")
    turn_idx = _turn_index(message, context=context)
    message_id = _evidence_message_id(simulation_id=simulation_id, turn_idx=turn_idx)
    if role == "user":
        tool_calls = message.get("tool_calls")
        if tool_calls not in (None, []):
            raise PacketBuildError(f"{context} user message unexpectedly contains tool calls")
        return {
            "role": "user",
            "content": _nonblank_string(message.get("content"), context=f"{context}.content"),
            "turn_idx": turn_idx,
            "source_message_id": message_id,
        }
    if role == "assistant":
        content = message.get("content")
        if content is not None:
            content = _nonblank_string(content, context=f"{context}.content")
        source_calls = message.get("tool_calls")
        if source_calls is None:
            source_calls = []
        if not isinstance(source_calls, list):
            raise PacketBuildError(f"{context}.tool_calls must be an array or null")
        calls: list[dict[str, Any]] = []
        for call_index, call in enumerate(source_calls):
            call_context = f"{context}.tool_calls[{call_index}]"
            if not isinstance(call, Mapping):
                raise PacketBuildError(f"{call_context} must be an object")
            source_call_id = _nonblank_string(call.get("id"), context=f"{call_context}.id")
            call_id = call_id_map.get(source_call_id)
            if call_id is None:
                raise PacketBuildError(f"{call_context}.id was not indexed")
            name = _nonblank_string(call.get("name"), context=f"{call_context}.name")
            arguments = call.get("arguments")
            if not isinstance(arguments, Mapping):
                raise PacketBuildError(f"{call_context}.arguments must be an object")
            if call.get("requestor") != "assistant":
                raise PacketBuildError(f"{call_context}.requestor must equal 'assistant'")
            _validate_call_arguments(
                name=name,
                arguments=arguments,
                definitions=definitions,
                context=call_context,
            )
            calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": dict(arguments),
                    "requestor": "assistant",
                }
            )
        if content is None and not calls:
            raise PacketBuildError(f"{context} assistant message has no content or tool calls")
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": calls or None,
            "turn_idx": turn_idx,
            "source_message_id": message_id,
        }
    if role == "tool":
        if message.get("requestor") != "assistant":
            raise PacketBuildError(f"{context}.requestor must equal 'assistant'")
        error = message.get("error")
        if not isinstance(error, bool):
            raise PacketBuildError(f"{context}.error must be boolean")
        source_call_id = _nonblank_string(message.get("id"), context=f"{context}.id")
        call_id = call_id_map.get(source_call_id)
        if call_id is None:
            raise PacketBuildError(f"{context}.id has no preceding source tool call")
        return {
            "role": "tool",
            "id": call_id,
            "content": _nonblank_string(message.get("content"), context=f"{context}.content"),
            "requestor": "assistant",
            "error": error,
            "turn_idx": turn_idx,
            "source_message_id": message_id,
        }
    raise PacketBuildError(f"{context} has unsupported role {role!r}")


def _target_and_terminal(
    messages: Sequence[Any], *, context: str
) -> tuple[int, Mapping[str, Any], str]:
    candidates = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, Mapping)
        and message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
        and bool(message["content"].strip())
    ]
    if not candidates:
        raise PacketBuildError(f"{context} has no textual assistant target")
    target_index = candidates[-1]
    target = messages[target_index]
    if not isinstance(target, Mapping):  # narrowed by the comprehension
        raise AssertionError("target mapping invariant")
    if target.get("tool_calls") not in (None, []):
        raise PacketBuildError(f"{context} target response contains tool calls")
    future = messages[target_index + 1 :]
    if len(future) != 1 or not isinstance(future[0], Mapping):
        raise PacketBuildError(
            f"{context} target must be followed by exactly one terminal user message"
        )
    terminal = future[0]
    if terminal.get("role") != "user":
        raise PacketBuildError(f"{context} future message is not the terminal user message")
    terminal_content = _nonblank_string(
        terminal.get("content"), context=f"{context}.terminal.content"
    )
    controls = TERMINAL_CONTROL_RE.findall(terminal_content)
    if len(controls) != 1:
        raise PacketBuildError(
            f"{context} terminal user message must contain exactly one reviewed control token"
        )
    return target_index, target, controls[0]


def _validate_source_turns(messages: Sequence[Any], *, context: str) -> None:
    turns: list[int] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise PacketBuildError(f"{context}.messages[{index}] must be an object")
        turns.append(_turn_index(message, context=f"{context}.messages[{index}]"))
    expected = list(range(len(messages)))
    if turns != expected:
        raise PacketBuildError(
            f"{context} source turn_idx values must be contiguous from zero: "
            f"expected={expected}, observed={turns}"
        )


def _example_id(simulation_id: str) -> str:
    digest = canonical_sha256(
        {"namespace": EXAMPLE_ID_NAMESPACE, "simulation_id": simulation_id}
    )
    return f"cs-{digest[:24]}"


def _call_id_map(
    messages: Sequence[Any], *, simulation_id: str, context: str
) -> dict[str, str]:
    source_call_ids: list[str] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise PacketBuildError(f"{context}.messages[{message_index}].tool_calls is not an array")
        for call_index, call in enumerate(calls):
            if not isinstance(call, Mapping):
                raise PacketBuildError(
                    f"{context}.messages[{message_index}].tool_calls[{call_index}] is not an object"
                )
            source_call_ids.append(
                _nonblank_string(
                    call.get("id"),
                    context=(
                        f"{context}.messages[{message_index}].tool_calls[{call_index}].id"
                    ),
                )
            )
    duplicates = sorted(
        call_id for call_id, count in Counter(source_call_ids).items() if count > 1
    )
    if duplicates:
        raise PacketBuildError(f"{context} has duplicate source tool call IDs: {duplicates}")
    return {
        source_call_id: _evidence_call_id(
            simulation_id=simulation_id,
            source_call_id=source_call_id,
        )
        for source_call_id in source_call_ids
    }


def _private_traceability(
    *,
    simulation_id: str,
    messages: Sequence[Any],
    target_index: int,
    call_id_map: Mapping[str, str],
    context: str,
) -> dict[str, Any]:
    message_map: list[dict[str, Any]] = []
    for index, message in enumerate(messages[: target_index + 1]):
        if not isinstance(message, Mapping):
            raise PacketBuildError(f"{context}.messages[{index}] must be an object")
        turn_idx = _turn_index(message, context=f"{context}.messages[{index}]")
        message_map.append(
            {
                "turn_idx": turn_idx,
                "role": message.get("role"),
                "evidence_message_id": _evidence_message_id(
                    simulation_id=simulation_id,
                    turn_idx=turn_idx,
                ),
                "source_provider_message_id": _provider_message_id(
                    message,
                    context=f"{context}.messages[{index}]",
                ),
            }
        )
    terminal = messages[target_index + 1]
    if not isinstance(terminal, Mapping):
        raise PacketBuildError(f"{context} terminal message must be an object")
    terminal_turn_idx = _turn_index(terminal, context=f"{context}.terminal")
    return {
        "visibility": "private_provenance_not_judge_or_annotator_input",
        "message_id_map": message_map,
        "tool_call_id_map": [
            {
                "evidence_call_id": evidence_call_id,
                "source_call_id": source_call_id,
            }
            for source_call_id, evidence_call_id in call_id_map.items()
        ],
        "excluded_terminal": {
            "turn_idx": terminal_turn_idx,
            "source_provider_message_id": _provider_message_id(
                terminal,
                context=f"{context}.terminal",
            ),
        },
    }


def build_evidence_packet(
    simulation: Mapping[str, Any],
    *,
    policy: str,
    tool_definitions: Sequence[ToolDefinition],
) -> BuiltPacket:
    """Build one packet using only the reviewed evidence-field allowlist."""
    simulation_id = _nonblank_string(simulation.get("id"), context="simulation.id")
    context = f"simulation[{simulation_id}]"
    if simulation.get("policy") != policy:
        raise PacketBuildError(f"{context} embedded policy differs from the pinned policy")
    messages = simulation.get("messages")
    if not isinstance(messages, list):
        raise PacketBuildError(f"{context}.messages must be an array")
    _validate_source_turns(messages, context=context)
    target_index, target, terminal_control = _target_and_terminal(messages, context=context)
    call_id_map = _call_id_map(
        messages[:target_index], simulation_id=simulation_id, context=context
    )
    ordered_definitions = list(tool_definitions)
    definition_map = {definition.name: definition for definition in ordered_definitions}
    if len(ordered_definitions) != len(definition_map):
        raise PacketBuildError("tool definition names must be unique")
    prefix = [
        _export_prefix_message(
            message,
            simulation_id=simulation_id,
            call_id_map=call_id_map,
            definitions=definition_map,
            context=f"{context}.messages[{index}]",
        )
        for index, message in enumerate(messages[:target_index])
        if isinstance(message, Mapping)
    ]
    if len(prefix) != target_index:
        raise PacketBuildError(f"{context} contains a non-object prefix message")

    example_id = _example_id(simulation_id)
    packet = EvidencePacket.model_validate(
        {
            "schema_version": "1.0",
            "example_id": example_id,
            "policy": policy,
            "tool_definitions": [
                definition.model_dump(mode="json") for definition in ordered_definitions
            ],
            "conversation_prefix": prefix,
            "target_response": {
                "role": "assistant",
                "content": _nonblank_string(
                    target.get("content"), context=f"{context}.target.content"
                ),
                "turn_idx": _turn_index(target, context=f"{context}.target"),
                "source_message_id": _evidence_message_id(
                    simulation_id=simulation_id,
                    turn_idx=_turn_index(target, context=f"{context}.target"),
                ),
            },
        }
    )
    packet_json = packet.model_dump(mode="json")
    packet_hash = canonical_sha256(packet_json)
    packet_size_bytes = len(canonical_json_bytes(packet_json))
    task_id = simulation.get("task_id")
    if isinstance(task_id, bool) or not isinstance(task_id, (str, int)):
        raise PacketBuildError(f"{context}.task_id must be a string or integer")
    if isinstance(task_id, str) and not task_id.strip():
        raise PacketBuildError(f"{context}.task_id must not be blank")
    trial = simulation.get("trial")
    if isinstance(trial, bool) or not isinstance(trial, int) or trial < 0:
        raise PacketBuildError(f"{context}.trial must be a nonnegative integer")
    manifest_entry = {
        "example_id": example_id,
        "packet_path": f"packets/{example_id}.json",
        "packet_hash": packet_hash,
        "packet_size_bytes": packet_size_bytes,
        "source_simulation_id": simulation_id,
        "source_simulation_hash": canonical_sha256(dict(simulation)),
        "task_id": task_id,
        "trial": trial,
        "target_turn_idx": packet.target_response.turn_idx,
        "target_evidence_message_id": packet.target_response.source_message_id,
        "terminal_control": terminal_control,
        "future_messages_excluded": 1,
        "prefix_message_count": len(packet.conversation_prefix),
        "tool_call_count": sum(
            len(message.tool_calls or [])
            for message in packet.conversation_prefix
            if message.role == "assistant"
        ),
        "private_traceability": _private_traceability(
            simulation_id=simulation_id,
            messages=messages,
            target_index=target_index,
            call_id_map=call_id_map,
            context=context,
        ),
    }
    return BuiltPacket(packet=packet, packet_hash=packet_hash, manifest_entry=manifest_entry)


def build_packet_corpus(
    *,
    trajectory_path: Path,
    policy_path: Path,
    tools_source_path: Path,
    compatibility_revision: str,
) -> CorpusBuild:
    """Build and validate the complete eligible trajectory frame offline."""
    source = _load_object(trajectory_path)
    simulations = source.get("simulations")
    if not isinstance(simulations, list):
        raise PacketBuildError("trajectory.simulations must be an array")
    policy = policy_path.read_text(encoding="utf-8")
    if not policy.strip():
        raise PacketBuildError("pinned policy is blank")
    definitions = reconstruct_tool_definitions(tools_source_path)
    definition_json = [definition.model_dump(mode="json") for definition in definitions]
    definition_hash = canonical_sha256(definition_json)
    if (
        compatibility_revision == PINNED_RETAIL_COMPATIBILITY_REVISION
        and definition_hash != PINNED_RETAIL_TOOL_DEFINITIONS_SHA256
    ):
        raise PacketBuildError(
            "reconstructed pinned retail tool definitions do not match the reviewed "
            f"Tool.openai_schema hash: expected={PINNED_RETAIL_TOOL_DEFINITIONS_SHA256}, "
            f"observed={definition_hash}"
        )

    built: list[BuiltPacket] = []
    for index, simulation in enumerate(simulations):
        if not isinstance(simulation, Mapping):
            raise PacketBuildError(f"trajectory.simulations[{index}] must be an object")
        built.append(
            build_evidence_packet(
                simulation,
                policy=policy,
                tool_definitions=definitions,
            )
        )

    example_ids = [item.packet.example_id for item in built]
    source_ids = [item.manifest_entry["source_simulation_id"] for item in built]
    if len(example_ids) != len(set(example_ids)):
        raise PacketBuildError("opaque example ID collision")
    if len(source_ids) != len(set(source_ids)):
        raise PacketBuildError("duplicate source simulation IDs")

    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "packet_builder_version": PACKET_BUILDER_VERSION,
        "visibility": "private_provenance_not_judge_or_annotator_input",
        "compatibility_revision": _nonblank_string(
            compatibility_revision, context="compatibility_revision"
        ),
        "source_artifact": trajectory_path.name,
        "source_sha256": sha256_file(trajectory_path),
        "policy_artifact": policy_path.name,
        "policy_sha256": sha256_file(policy_path),
        "tools_source_artifact": tools_source_path.name,
        "tools_source_sha256": sha256_file(tools_source_path),
        "tool_definitions_hash": definition_hash,
        "tool_definition_order": "pinned_toolkit_source_order",
        "tool_definition_count": len(definitions),
        "source_simulation_count": len(simulations),
        "included_packet_count": len(built),
        "excluded_simulation_count": 0,
        "entries": [item.manifest_entry for item in built],
    }
    manifest["manifest_hash"] = canonical_sha256(manifest)
    return CorpusBuild(packets=tuple(built), manifest=manifest)


def write_packet_corpus(build: CorpusBuild, output_root: Path) -> None:
    """Write a corpus rooted at ``output_root``; manifest paths are relative to it."""
    manifest_without_hash = dict(build.manifest)
    recorded_manifest_hash = manifest_without_hash.pop("manifest_hash", None)
    if recorded_manifest_hash != canonical_sha256(manifest_without_hash):
        raise PacketBuildError("corpus manifest hash does not match its content")
    entries = build.manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != len(build.packets):
        raise PacketBuildError("corpus manifest entries do not match packet count")
    entries_by_id = {
        entry.get("example_id"): entry for entry in entries if isinstance(entry, Mapping)
    }
    if len(entries_by_id) != len(entries):
        raise PacketBuildError("corpus manifest contains duplicate or malformed entries")

    packet_dir = safe_join(output_root, "packets")
    packet_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {f"{built.packet.example_id}.json" for built in build.packets}
    stale_names = {path.name for path in packet_dir.glob("*.json")} - expected_names
    if stale_names:
        raise PacketBuildError(
            f"packet output contains files outside this build: {sorted(stale_names)}"
        )
    for built in build.packets:
        entry = entries_by_id.get(built.packet.example_id)
        expected_path = f"packets/{built.packet.example_id}.json"
        if entry is None or entry.get("packet_path") != expected_path:
            raise PacketBuildError(
                f"manifest packet_path mismatch for {built.packet.example_id}"
            )
        packet_json = built.packet.model_dump(mode="json")
        if built.packet_hash != canonical_sha256(packet_json):
            raise PacketBuildError(f"packet hash mismatch for {built.packet.example_id}")
        if entry.get("packet_hash") != built.packet_hash:
            raise PacketBuildError(
                f"manifest packet hash mismatch for {built.packet.example_id}"
            )
        write_json_atomic(
            safe_join(packet_dir, f"{built.packet.example_id}.json"),
            packet_json,
        )
    write_json_atomic(safe_join(output_root, "corpus_manifest.json"), build.manifest)
