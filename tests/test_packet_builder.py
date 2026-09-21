from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from judge_compare.hashing import canonical_json_bytes, canonical_sha256
from judge_compare.packet_builder import (
    PINNED_RETAIL_TOOL_DEFINITIONS_SHA256,
    PacketBuildError,
    build_evidence_packet,
    build_packet_corpus,
    reconstruct_tool_definitions,
    write_packet_corpus,
)

PIN = "17e07b1da2bbc0cadfddeea36412686e0604127b"
FIXTURE_REVISION = "fixture-revision"
SOURCE_SHA256 = "f344a3a63783018b693f2a1a60b80b9d4f86fce6d5df74c0fcba647baacbea00"


def _tool_source() -> str:
    return '''\
from typing import List

class Tools:
    @is_tool(ToolType.READ)
    def lookup(self, key: str, tags: List[str]) -> str:
        """Look up one record using its key and tags.

        Args:
            key: The record key.
            tags: Tags that constrain the lookup.

        Returns:
            The matching record.
        """
        return key
'''


def _simulation(policy: str) -> dict:
    return {
        "id": "source-simulation-1",
        "task_id": "task-7",
        "trial": 2,
        "policy": policy,
        "reward_info": {"reward": 1},
        "messages": [
            {
                "role": "assistant",
                "content": "Hello.",
                "tool_calls": None,
                "turn_idx": 0,
                "usage": {"prompt_tokens": 1},
                "raw_data": None,
            },
            {
                "role": "user",
                "content": "Please check item x.",
                "tool_calls": None,
                "turn_idx": 1,
                "cost": 99,
                "raw_data": {"id": "provider-user-1", "model": "hidden"},
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "toolu_provider_call_1",
                        "name": "lookup",
                        "arguments": {"key": "x", "tags": ["open"]},
                        "requestor": "assistant",
                        "ignored": "must not leak",
                    }
                ],
                "turn_idx": 2,
                "raw_data": {"id": "provider-assistant-1", "secret": "hidden"},
            },
            {
                "role": "tool",
                "id": "toolu_provider_call_1",
                "content": "found",
                "requestor": "assistant",
                "error": False,
                "turn_idx": 3,
                "timestamp": "ignored",
            },
            {
                "role": "assistant",
                "content": "Item x is available.",
                "tool_calls": None,
                "turn_idx": 4,
                "raw_data": {"id": "provider-target-1", "model": "hidden"},
                "usage": {"output_tokens": 5},
            },
            {
                "role": "user",
                "content": "Thanks. **###STOP###**",
                "tool_calls": None,
                "turn_idx": 5,
                "raw_data": {"id": "provider-future-1"},
            },
        ],
    }


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    policy_path = tmp_path / "retail-policy.md"
    tools_path = tmp_path / "retail-tools.py"
    trajectory_path = tmp_path / "trajectory.json"
    policy_path.write_text("Use verified records only.\n", encoding="utf-8")
    tools_path.write_text(_tool_source(), encoding="utf-8")
    trajectory_path.write_text(
        json.dumps({"simulations": [_simulation(policy_path.read_text(encoding="utf-8"))]}),
        encoding="utf-8",
    )
    return trajectory_path, policy_path, tools_path


def test_reconstructs_strict_tool_schema_from_reviewed_source(tmp_path: Path) -> None:
    tools_path = tmp_path / "retail-tools.py"
    tools_path.write_text(_tool_source(), encoding="utf-8")

    definitions = reconstruct_tool_definitions(tools_path)

    assert [definition.name for definition in definitions] == ["lookup"]
    parameters = definitions[0].parameters
    assert parameters["required"] == ["key", "tags"]
    assert parameters["title"] == "parameters"
    assert "additionalProperties" not in parameters
    assert parameters["properties"] == {
        "key": {
            "type": "string",
            "description": "The record key.",
            "title": "Key",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags that constrain the lookup.",
            "title": "Tags",
        },
    }


def test_pinned_tool_schema_matches_reviewed_upstream_generator() -> None:
    repository = Path(__file__).resolve().parents[1]
    tools_path = (
        repository
        / "data/raw/tau2/claude-sonnet-4-5_sierra_2026-02-26"
        / "compatibility"
        / PIN
        / "retail-tools.py"
    )

    definitions = reconstruct_tool_definitions(tools_path)
    definition_json = [definition.model_dump(mode="json") for definition in definitions]

    assert len(PINNED_RETAIL_TOOL_DEFINITIONS_SHA256) == 64
    assert canonical_sha256(definition_json) == PINNED_RETAIL_TOOL_DEFINITIONS_SHA256
    assert PINNED_RETAIL_TOOL_DEFINITIONS_SHA256 == (
        "10637249d35710a3aece79a3cc22c033bd09c0e0692393728cb138e0b9d92a2a"
    )
    assert [definition.name for definition in definitions[:5]] == [
        "calculate",
        "cancel_pending_order",
        "exchange_delivered_order_items",
        "find_user_id_by_name_zip",
        "find_user_id_by_email",
    ]
    by_name = {definition.name: definition for definition in definitions}
    assert "required" not in by_name["list_all_product_types"].parameters
    assert "additionalProperties" not in by_name["calculate"].parameters
    assert by_name["cancel_pending_order"].description.startswith(
        "Cancel a pending order. If the order is already processed or delivered,\n\nit cannot"
    )
    exchange_parameters = by_name["exchange_delivered_order_items"].parameters
    assert "\n" in exchange_parameters["properties"]["new_item_ids"]["description"]


def test_packet_export_is_allowlisted_and_excludes_future_terminal(tmp_path: Path) -> None:
    trajectory_path, policy_path, tools_path = _write_fixture(tmp_path)

    build = build_packet_corpus(
        trajectory_path=trajectory_path,
        policy_path=policy_path,
        tools_source_path=tools_path,
        compatibility_revision=FIXTURE_REVISION,
    )

    assert len(build.packets) == 1
    built = build.packets[0]
    payload = built.packet.model_dump(mode="json")
    serialized = json.dumps(payload)
    assert set(payload) == {
        "schema_version",
        "example_id",
        "policy",
        "tool_definitions",
        "conversation_prefix",
        "target_response",
    }
    source_only_keys = {
        "agent_model",
        "cost",
        "model",
        "provider",
        "raw_data",
        "reward",
        "reward_info",
        "source_model",
        "usage",
    }

    def all_keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(all_keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(all_keys(child) for child in value))
        return set()

    assert not (source_only_keys & all_keys(payload))
    assert "provider-" not in serialized
    assert "toolu_" not in serialized
    assert "hidden" not in serialized
    assert "source-simulation-1" not in serialized
    assert all(
        message["source_message_id"].startswith("msg-")
        for message in payload["conversation_prefix"]
    )
    assert payload["target_response"]["source_message_id"].startswith("msg-")
    evidence_call_id = payload["conversation_prefix"][2]["tool_calls"][0]["id"]
    assert evidence_call_id.startswith("call-")
    assert payload["conversation_prefix"][3]["id"] == evidence_call_id
    assert built.manifest_entry["terminal_control"] == "STOP"
    assert built.packet_hash == canonical_sha256(payload)
    assert built.manifest_entry["packet_size_bytes"] == len(canonical_json_bytes(payload))
    traceability = built.manifest_entry["private_traceability"]
    assert traceability["visibility"] == "private_provenance_not_judge_or_annotator_input"
    assert traceability["tool_call_id_map"] == [
        {
            "evidence_call_id": evidence_call_id,
            "source_call_id": "toolu_provider_call_1",
        }
    ]
    assert any(
        item["source_provider_message_id"] == "provider-target-1"
        for item in traceability["message_id_map"]
    )
    assert (
        traceability["excluded_terminal"]["source_provider_message_id"]
        == "provider-future-1"
    )


def test_packet_build_rejects_argument_or_result_mismatch(tmp_path: Path) -> None:
    _, policy_path, tools_path = _write_fixture(tmp_path)
    definitions = reconstruct_tool_definitions(tools_path)
    policy = policy_path.read_text(encoding="utf-8")
    bad_argument = _simulation(policy)
    bad_argument["messages"][2]["tool_calls"][0]["arguments"]["unknown"] = True

    with pytest.raises(PacketBuildError, match="unexpected=\\['unknown'\\]"):
        build_evidence_packet(bad_argument, policy=policy, tool_definitions=definitions)

    bad_result = _simulation(policy)
    bad_result["messages"][3]["id"] = "wrong-result-id"
    with pytest.raises(PacketBuildError, match="no preceding source tool call"):
        build_evidence_packet(bad_result, policy=policy, tool_definitions=definitions)


def test_corpus_and_written_manifest_are_deterministic_and_portable(tmp_path: Path) -> None:
    trajectory_path, policy_path, tools_path = _write_fixture(tmp_path)
    first = build_packet_corpus(
        trajectory_path=trajectory_path,
        policy_path=policy_path,
        tools_source_path=tools_path,
        compatibility_revision=FIXTURE_REVISION,
    )
    second = build_packet_corpus(
        trajectory_path=trajectory_path,
        policy_path=policy_path,
        tools_source_path=tools_path,
        compatibility_revision=FIXTURE_REVISION,
    )

    assert first.manifest == second.manifest
    manifest_without_hash = dict(first.manifest)
    manifest_hash = manifest_without_hash.pop("manifest_hash")
    assert manifest_hash == canonical_sha256(manifest_without_hash)
    assert first.manifest["entries"][0]["packet_path"].startswith("packets/")
    assert str(tmp_path) not in json.dumps(first.manifest)

    output_root = tmp_path / "output"
    write_packet_corpus(first, output_root)
    written_packet = json.loads(next((output_root / "packets").glob("*.json")).read_text())
    written_manifest = json.loads((output_root / "corpus_manifest.json").read_text())
    assert canonical_sha256(written_packet) == first.packets[0].packet_hash
    assert written_manifest == first.manifest


def test_writer_rejects_manifest_path_mismatch_and_output_escape(tmp_path: Path) -> None:
    trajectory_path, policy_path, tools_path = _write_fixture(tmp_path)
    build = build_packet_corpus(
        trajectory_path=trajectory_path,
        policy_path=policy_path,
        tools_source_path=tools_path,
        compatibility_revision=FIXTURE_REVISION,
    )
    tampered_manifest = deepcopy(build.manifest)
    tampered_manifest["entries"][0]["packet_path"] = "../escape.json"
    tampered_manifest.pop("manifest_hash")
    tampered_manifest["manifest_hash"] = canonical_sha256(tampered_manifest)

    with pytest.raises(PacketBuildError, match="packet_path mismatch"):
        write_packet_corpus(replace(build, manifest=tampered_manifest), tmp_path / "bad-output")

    output_root = tmp_path / "symlink-output"
    output_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (output_root / "packets").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes configured root"):
        write_packet_corpus(build, output_root)


def test_approved_archive_builds_all_packets_and_validates_all_calls() -> None:
    repository = Path(__file__).resolve().parents[1]
    source_root = (
        repository / "data/raw/tau2/claude-sonnet-4-5_sierra_2026-02-26"
    )
    compatibility = source_root / "compatibility" / PIN

    build = build_packet_corpus(
        trajectory_path=(
            source_root / "claude-sonnet-4-5_enabled_retail_gpt-5.2_4trials.json"
        ),
        policy_path=compatibility / "retail-policy.md",
        tools_source_path=compatibility / "retail-tools.py",
        compatibility_revision=PIN,
    )

    assert build.manifest["source_sha256"] == SOURCE_SHA256
    assert build.manifest["source_simulation_count"] == 456
    assert build.manifest["included_packet_count"] == 456
    assert build.manifest["tool_definition_count"] == 16
    assert len(PINNED_RETAIL_TOOL_DEFINITIONS_SHA256) == 64
    assert (
        build.manifest["tool_definitions_hash"]
        == PINNED_RETAIL_TOOL_DEFINITIONS_SHA256
        == "10637249d35710a3aece79a3cc22c033bd09c0e0692393728cb138e0b9d92a2a"
    )
    assert build.manifest["tool_definition_order"] == "pinned_toolkit_source_order"
    assert [definition.name for definition in build.packets[0].packet.tool_definitions] == [
        "calculate",
        "cancel_pending_order",
        "exchange_delivered_order_items",
        "find_user_id_by_name_zip",
        "find_user_id_by_email",
        "get_order_details",
        "get_product_details",
        "get_item_details",
        "get_user_details",
        "list_all_product_types",
        "modify_pending_order_address",
        "modify_pending_order_items",
        "modify_pending_order_payment",
        "modify_user_address",
        "return_delivered_order_items",
        "transfer_to_human_agents",
    ]
    assert sum(entry["tool_call_count"] for entry in build.manifest["entries"]) == 3220
    assert Counter(entry["terminal_control"] for entry in build.manifest["entries"]) == {
        "STOP": 359,
        "TRANSFER": 96,
        "OUT-OF-SCOPE": 1,
    }
