from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

import judge_compare.request_fit as request_fit
from judge_compare.config import ExperimentConfig, load_config
from judge_compare.hashing import canonical_json_bytes, canonical_sha256, sha256_bytes
from judge_compare.models import EvidencePacket
from judge_compare.request_fit import (
    CRITERIA,
    JEV_EXACT_COUNTER_BLOCKER,
    JEV_QUESTION_TEXT,
    OPENAI_COUNTED_CREATE_FIELDS,
    AppendOnlyCountEvidenceStore,
    ContextExclusionRequiresAmendment,
    CountEvidenceError,
    ExactCounterUnavailable,
    ExplicitAuthorizationRequired,
    JevExactTokenCounts,
    JudgeRequestEnvelopes,
    RequestFitError,
    VersionedRubric,
    build_judge_request_envelopes,
    enforce_joint_request_fit,
    execute_jev_exact_count,
    execute_openai_exact_count,
)

ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _packet() -> EvidencePacket:
    return EvidencePacket.model_validate(
        {
            "schema_version": "1.0",
            "example_id": "opaque-request-fit-example",
            "policy": "Only promise an action after its successful tool result.",
            "tool_definitions": [
                {
                    "name": "lookup",
                    "description": "Look up the order.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "title": "parameters",
                    },
                }
            ],
            "conversation_prefix": [
                {"role": "user", "content": "Was it refunded?", "turn_idx": 0},
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
                    "content": "refund failed",
                    "requestor": "assistant",
                    "error": False,
                    "turn_idx": 2,
                },
            ],
            "target_response": {
                "role": "assistant",
                "content": "Your refund completed.",
                "turn_idx": 3,
            },
        }
    )


def _rubric() -> VersionedRubric:
    content = "Rubric version 1. Grounding, relevance, and policy compliance are independent."
    return VersionedRubric(
        version="customer-support-rubric-v1-test",
        content=content,
        sha256=sha256_bytes(content.encode("utf-8")),
    )


def _envelope() -> JudgeRequestEnvelopes:
    return build_judge_request_envelopes(
        _packet(),
        _rubric(),
        load_config(ROOT / "configs" / "experiment.yaml"),
    )


class _FakeRawResponse:
    def __init__(self, input_tokens: int) -> None:
        self.headers = {"x-request-id": "count-request-1"}
        self._input_tokens = input_tokens

    def parse(self) -> SimpleNamespace:
        return SimpleNamespace(
            input_tokens=self._input_tokens,
            object="response.input_tokens",
        )


class _FakeCountResource:
    def __init__(self, input_tokens: int, captured: dict[str, Any]) -> None:
        self._input_tokens = input_tokens
        self._captured = captured
        self.with_raw_response = self

    def count(self, **body: Any) -> _FakeRawResponse:
        self._captured["body"] = body
        return _FakeRawResponse(self._input_tokens)


class _FakeOpenAIClient:
    def __init__(self, input_tokens: int, captured: dict[str, Any]) -> None:
        self.responses = SimpleNamespace(
            input_tokens=_FakeCountResource(input_tokens, captured)
        )
        self._captured = captured

    def close(self) -> None:
        self._captured["closed"] = True


def _openai_factory(input_tokens: int, captured: dict[str, Any]):
    def factory(**kwargs: Any) -> _FakeOpenAIClient:
        captured["client_kwargs"] = kwargs
        return _FakeOpenAIClient(input_tokens, captured)

    return factory


def _jev_counts(
    *,
    total: int = 20_000,
    grounding: int = 10_100,
    relevance: int = 10_050,
    policy_compliance: int = 10_075,
) -> JevExactTokenCounts:
    return JevExactTokenCounts(
        total_request_tokens=total,
        state_plus_question_tokens={
            "grounding": grounding,
            "relevance": relevance,
            "policy_compliance": policy_compliance,
        },
        counter_name="reviewed-exact-jev-counter",
        counter_version="1.0.0",
        counter_implementation_sha256="a" * 64,
        approval_record_sha256="b" * 64,
        provider_request_id="jev-count-1",
    )


def _jev_evidence(
    envelope: JudgeRequestEnvelopes,
    counts: JevExactTokenCounts | None = None,
):
    return request_fit._jev_count_evidence(
        envelope,
        observed=counts or _jev_counts(),
        observed_at=OBSERVED_AT,
    )


def _openai_evidence(envelope: JudgeRequestEnvelopes, input_tokens: int = 10_000):
    return request_fit._openai_count_evidence(
        envelope,
        input_tokens=input_tokens,
        provider_request_id="count-request-1",
        observed_at=OBSERVED_AT,
    )


def _reseal_envelope(raw: dict[str, Any]) -> None:
    state = raw["jev_system_one_body"]["state"]
    state_text = canonical_json_bytes(state).decode("utf-8")
    raw["gpt_response_create_body"]["input"] = state_text
    raw["gpt_input_token_count_body"] = request_fit._gpt_count_body_from_create(
        raw["gpt_response_create_body"]
    )
    raw["shared_state_hash"] = canonical_sha256(state)
    raw["gpt_response_create_body_hash"] = canonical_sha256(raw["gpt_response_create_body"])
    raw["gpt_input_token_count_body_hash"] = canonical_sha256(raw["gpt_input_token_count_body"])
    raw["jev_system_one_body_hash"] = canonical_sha256(raw["jev_system_one_body"])
    raw["envelope_hash"] = canonical_sha256(
        {
            key: raw[key]
            for key in (
                "schema_version",
                "example_id",
                "packet_hash",
                "rubric_version",
                "rubric_hash",
                "config_hash",
                "shared_state_hash",
                "gpt_response_create_body_hash",
                "gpt_input_token_count_body_hash",
                "jev_system_one_body_hash",
            )
        }
    )


def test_builder_creates_semantically_equivalent_canonical_requests() -> None:
    first = _envelope()
    second = _envelope()

    assert first == second
    assert first.gpt_response_create_bytes() == second.gpt_response_create_bytes()
    assert first.jev_system_one_bytes() == second.jev_system_one_bytes()
    assert canonical_sha256(first.gpt_response_create_body) == (
        first.gpt_response_create_body_hash
    )

    state = first.jev_system_one_body["state"]
    assert json.loads(first.gpt_response_create_body["input"]) == state
    assert first.shared_state_hash == canonical_sha256(state)
    assert state["evidence_packet"]["example_id"] == first.example_id
    assert state["rubric"]["version"] == first.rubric_version
    assert state["rubric"]["sha256"] == first.rubric_hash
    assert canonical_sha256(first.config_snapshot) == first.config_hash


def test_gpt_request_uses_pinned_settings_and_strict_probability_schema() -> None:
    envelope = _envelope()
    body = envelope.gpt_response_create_body

    assert body["model"] == "gpt-5.4-2026-03-05"
    assert body["reasoning"] == {"effort": "medium"}
    assert body["tools"] == []
    assert body["store"] is False
    assert body["service_tier"] == "default"
    assert body["truncation"] == "disabled"
    assert body["max_output_tokens"] == 25_000
    assert "temperature" not in body
    assert "top_p" not in body
    assert "seed" not in body

    output_format = body["text"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    schema = output_format["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(CRITERIA)
    assert set(schema["properties"]) == set(CRITERIA)
    for criterion in CRITERIA:
        criterion_schema = schema["properties"][criterion]
        assert criterion_schema == {
            "type": "object",
            "additionalProperties": False,
            "properties": {"p_pass": {"type": "number", "minimum": 0, "maximum": 1}},
            "required": ["p_pass"],
        }


def test_count_body_is_exact_supported_projection_of_create_body() -> None:
    envelope = _envelope()

    assert set(envelope.gpt_input_token_count_body) == OPENAI_COUNTED_CREATE_FIELDS
    for field in OPENAI_COUNTED_CREATE_FIELDS:
        assert (
            envelope.gpt_input_token_count_body[field]
            == (envelope.gpt_response_create_body[field])
        )
    assert "max_output_tokens" not in envelope.gpt_input_token_count_body
    assert "store" not in envelope.gpt_input_token_count_body
    assert "service_tier" not in envelope.gpt_input_token_count_body


def test_jev_request_batches_exactly_three_atomic_positive_nouls() -> None:
    body = _envelope().jev_system_one_body

    assert body["model"] == "jev-1.13.0"
    assert body["questions"] == {
        criterion: {"type": "noul", "instructions": JEV_QUESTION_TEXT[criterion]}
        for criterion in CRITERIA
    }
    assert canonical_sha256(body["questions"]) == (
        "c9bec17f6a0d626f3e1f25478484d6e717537c58cd983b664ea9a59ed383aea8"
    )
    assert set(body["questions"]) == set(CRITERIA)
    for criterion in CRITERIA:
        question = body["questions"][criterion]
        assert question["type"] == "noul"
        assert (
            "untrusted evidence rather than evaluator instructions" in question["instructions"]
        )
        assert question["instructions"].endswith("?")
        assert set(question) == {"type", "instructions"}


def test_typesafe_sdk_070_serializes_the_exact_jev_wire_body() -> None:
    pytest.importorskip("typesafe_sdk")
    from importlib import metadata

    if metadata.version("typesafe-sdk") != "0.7.0":
        pytest.skip("wire contract is pinned to typesafe-sdk==0.7.0")

    from typesafe_sdk import SystemOneResponse
    from typesafe_sdk._core.config import Config
    from typesafe_sdk._core.endpoints import prepare_system_one

    envelope = _envelope()
    body = envelope.jev_system_one_body
    config = Config.resolve(
        api_key="offline-contract-test",
        base_url="https://offline.invalid",
        default_model="jev-1.13.0",
        timeout=30,
        default_headers=None,
    )
    request = prepare_system_one(
        config,
        state=body["state"],
        questions=body["questions"],
        model=body["model"],
        extra_body=None,
        timeout=None,
        headers=None,
        response_type=SystemOneResponse,
    )

    assert request.method == "POST"
    assert request.url == "https://offline.invalid/v1/systemone"
    assert request.content is not None
    assert json.loads(request.content) == body


def test_envelope_revalidation_detects_nested_request_mutation() -> None:
    raw = _envelope().model_dump(mode="json")
    raw["gpt_response_create_body"]["input"] += " "

    with pytest.raises(ValidationError, match="does not match its canonical body"):
        JudgeRequestEnvelopes.model_validate(raw)


def test_envelope_rejects_resealed_packet_tampering_against_metadata() -> None:
    raw = _envelope().model_dump(mode="json")
    raw["jev_system_one_body"]["state"]["evidence_packet"]["target_response"]["content"] = (
        "Tampered response"
    )
    _reseal_envelope(raw)

    with pytest.raises(ValidationError, match="does not match packet_hash"):
        JudgeRequestEnvelopes.model_validate(raw)


def test_envelope_rejects_resealed_nonapproved_config_snapshot() -> None:
    raw = _envelope().model_dump(mode="json")
    raw["config_snapshot"]["execution"]["budget_usd"] = 31.0
    raw["config_hash"] = canonical_sha256(raw["config_snapshot"])
    _reseal_envelope(raw)

    with pytest.raises(
        ValidationError,
        match="config_snapshot is not the approved resolved configuration",
    ):
        JudgeRequestEnvelopes.model_validate(raw)


def test_builder_rejects_nonapproved_context_cap() -> None:
    with (ROOT / "configs" / "experiment.yaml").open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    raw["judges"]["frontier"]["max_input_tokens"] = 31_999
    config = ExperimentConfig.model_validate(raw)

    with pytest.raises(RequestFitError, match="approved 32k input cap"):
        build_judge_request_envelopes(_packet(), _rubric(), config)


def test_builder_revalidates_models_mutated_after_construction() -> None:
    packet = _packet()
    packet.example_id = ""
    with pytest.raises(ValidationError):
        build_judge_request_envelopes(
            packet,
            _rubric(),
            load_config(ROOT / "configs" / "experiment.yaml"),
        )

    rubric = _rubric()
    rubric.content = "mutated without updating its hash"
    with pytest.raises(ValidationError, match="rubric content hash mismatch"):
        build_judge_request_envelopes(
            _packet(),
            rubric,
            load_config(ROOT / "configs" / "experiment.yaml"),
        )

    config = load_config(ROOT / "configs" / "experiment.yaml")
    assert not isinstance(config.judges.frontier.max_output_tokens, str)
    config.judges.frontier.max_output_tokens = 24_999
    with pytest.raises(RequestFitError, match="approved 25k output cap"):
        build_judge_request_envelopes(_packet(), _rubric(), config)


def test_versioned_rubric_rejects_content_hash_mismatch() -> None:
    with pytest.raises(ValidationError, match="rubric content hash mismatch"):
        VersionedRubric(
            version="v1",
            content="rubric",
            sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("authorize_network", "api_key"),
    [(False, "secret"), (True, None), (True, "  ")],
)
def test_openai_counter_requires_explicit_flag_and_credential_before_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorize_network: bool,
    api_key: str | None,
) -> None:
    called = False

    def factory(**_: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("factory must not be called")

    monkeypatch.setattr(request_fit, "_pinned_openai_client_factory", factory)
    with pytest.raises(ExplicitAuthorizationRequired):
        execute_openai_exact_count(
            _envelope(),
            authorize_network=authorize_network,
            api_key=api_key,
            evidence_store=AppendOnlyCountEvidenceStore(tmp_path / "evidence"),
        )

    assert called is False
    assert not (tmp_path / "evidence").exists()


def test_public_exact_count_interfaces_do_not_accept_untrusted_counter_injection() -> None:
    assert "client_factory" not in signature(execute_openai_exact_count).parameters
    assert "counter" not in signature(execute_jev_exact_count).parameters


def test_openai_evidence_requires_provider_request_id() -> None:
    with pytest.raises(CountEvidenceError, match="x-request-id"):
        request_fit._openai_count_evidence(
            _envelope(),
            input_tokens=1,
            provider_request_id=None,
            observed_at=OBSERVED_AT,
        )


def test_openai_exact_count_records_append_only_evidence_without_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    store = AppendOnlyCountEvidenceStore(tmp_path / "evidence")
    envelope = _envelope()
    monkeypatch.setattr(
        request_fit,
        "_pinned_openai_client_factory",
        _openai_factory(12_345, captured),
    )

    execution = execute_openai_exact_count(
        envelope,
        authorize_network=True,
        api_key="test-secret-not-for-disk",
        evidence_store=store,
        observed_at=OBSERVED_AT,
    )

    assert captured["client_kwargs"] == {
        "api_key": "test-secret-not-for-disk",
        "base_url": "https://api.openai.com/v1",
        "max_retries": 0,
        "timeout": 300,
    }
    assert captured["body"] == envelope.gpt_input_token_count_body
    assert captured["closed"] is True
    assert execution.evidence.input_tokens == 12_345
    assert execution.evidence.fit_status == "fits"
    written = execution.evidence_path.read_text(encoding="utf-8")
    assert "test-secret-not-for-disk" not in written
    sealed = json.loads(written)
    evidence_hash = sealed.pop("evidence_hash")
    assert evidence_hash == canonical_sha256(sealed)

    with pytest.raises(CountEvidenceError, match="refusing to overwrite"):
        store.append(execution.evidence)


def test_append_only_store_does_not_use_example_id_as_a_path(tmp_path: Path) -> None:
    raw_packet = _packet().model_dump(mode="json")
    raw_packet["example_id"] = "../../outside/evidence"
    envelope = build_judge_request_envelopes(
        EvidencePacket.model_validate(raw_packet),
        _rubric(),
        load_config(ROOT / "configs" / "experiment.yaml"),
    )
    store = AppendOnlyCountEvidenceStore(tmp_path / "evidence")

    written = store.append(_openai_evidence(envelope))

    assert written.parent == store.root.resolve()
    assert "outside" not in written.name
    assert len(list(store.root.iterdir())) == 1


def test_openai_limit_exclusion_is_preserved_then_requires_amendment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        request_fit,
        "_pinned_openai_client_factory",
        _openai_factory(32_001, captured),
    )

    with pytest.raises(
        ContextExclusionRequiresAmendment,
        match="approved amendment before reselection",
    ) as raised:
        execute_openai_exact_count(
            _envelope(),
            authorize_network=True,
            api_key="secret",
            evidence_store=AppendOnlyCountEvidenceStore(tmp_path / "evidence"),
            observed_at=OBSERVED_AT,
        )

    assert raised.value.evidence_path is not None
    evidence = json.loads(raised.value.evidence_path.read_text(encoding="utf-8"))
    assert evidence["fit_status"] == "exceeds_approved_limit"
    assert evidence["amendment_required"] is True


def test_jev_fails_closed_without_a_reviewed_exact_counter(tmp_path: Path) -> None:
    with pytest.raises(ExactCounterUnavailable) as raised:
        execute_jev_exact_count(
            _envelope(),
            authorize_network=True,
            api_key="secret",
            evidence_store=AppendOnlyCountEvidenceStore(tmp_path / "evidence"),
        )

    assert str(raised.value) == JEV_EXACT_COUNTER_BLOCKER
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    "counts",
    [
        _jev_counts(total=64_001),
        _jev_counts(total=40_000, grounding=32_001),
    ],
)
def test_each_jev_limit_exclusion_requires_amendment(
    monkeypatch: pytest.MonkeyPatch,
    counts: JevExactTokenCounts,
) -> None:
    monkeypatch.setattr(
        request_fit,
        "APPROVED_JEV_COUNTER_IMPLEMENTATION_SHA256",
        "a" * 64,
    )
    monkeypatch.setattr(
        request_fit,
        "APPROVED_JEV_COUNTER_APPROVAL_SHA256",
        "b" * 64,
    )
    envelope = _envelope()

    with pytest.raises(ContextExclusionRequiresAmendment):
        enforce_joint_request_fit(
            envelope,
            openai_evidence=_openai_evidence(envelope),
            jev_evidence=_jev_evidence(envelope, counts),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "total_request_tokens": -1,
            "state_plus_question_tokens": {
                "grounding": 1,
                "relevance": 1,
                "policy_compliance": 1,
            },
        },
        {
            "total_request_tokens": 4,
            "state_plus_question_tokens": {
                "grounding": 5,
                "relevance": 1,
                "policy_compliance": 1,
            },
        },
    ],
)
def test_jev_exact_counts_reject_impossible_values(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JevExactTokenCounts.model_validate(
            {
                **payload,
                "counter_name": "reviewed-exact-jev-counter",
                "counter_version": "1.0.0",
                "counter_implementation_sha256": "a" * 64,
                "approval_record_sha256": "b" * 64,
            }
        )


def test_joint_fit_requires_bound_in_cap_evidence_from_both_judges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        request_fit,
        "APPROVED_JEV_COUNTER_IMPLEMENTATION_SHA256",
        "a" * 64,
    )
    monkeypatch.setattr(
        request_fit,
        "APPROVED_JEV_COUNTER_APPROVAL_SHA256",
        "b" * 64,
    )
    envelope = _envelope()

    resolution = enforce_joint_request_fit(
        envelope,
        openai_evidence=_openai_evidence(envelope),
        jev_evidence=_jev_evidence(envelope),
    )

    assert resolution.status == "eligible_for_both_judges"
    assert resolution.truncation_used is False
    assert resolution.amendment_required is False


def test_joint_fit_rejects_evidence_bound_to_another_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        request_fit,
        "APPROVED_JEV_COUNTER_IMPLEMENTATION_SHA256",
        "a" * 64,
    )
    monkeypatch.setattr(
        request_fit,
        "APPROVED_JEV_COUNTER_APPROVAL_SHA256",
        "b" * 64,
    )
    envelope = _envelope()
    other_packet = deepcopy(_packet().model_dump(mode="json"))
    other_packet["example_id"] = "other-example"
    other_envelope = build_judge_request_envelopes(
        EvidencePacket.model_validate(other_packet),
        _rubric(),
        load_config(ROOT / "configs" / "experiment.yaml"),
    )
    with pytest.raises(CountEvidenceError, match="does not match its envelope"):
        enforce_joint_request_fit(
            envelope,
            openai_evidence=_openai_evidence(other_envelope),
            jev_evidence=_jev_evidence(envelope),
        )


def test_joint_fit_fails_closed_without_pinned_jev_counter_approval() -> None:
    envelope = _envelope()

    with pytest.raises(ExactCounterUnavailable, match="does not expose"):
        enforce_joint_request_fit(
            envelope,
            openai_evidence=_openai_evidence(envelope),
            jev_evidence=_jev_evidence(envelope),
        )


def test_joint_fit_revalidates_a_mutated_envelope_before_evidence() -> None:
    envelope = _envelope()
    openai_evidence = _openai_evidence(envelope)
    jev_evidence = _jev_evidence(envelope)
    envelope.gpt_response_create_body["input"] += " "

    with pytest.raises(ValidationError):
        enforce_joint_request_fit(
            envelope,
            openai_evidence=openai_evidence,
            jev_evidence=jev_evidence,
        )
