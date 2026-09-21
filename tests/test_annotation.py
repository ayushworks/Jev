from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from judge_compare.annotation import (
    BLINDED_EXPORT_FORBIDDEN_KEYS,
    AdjudicatedAnnotationRecord,
    AdjudicatedCriteria,
    AdjudicatedCriterionAnnotation,
    AnnotationValidationError,
    BlindedAnnotationAssignment,
    RawAnnotationSubmission,
    RawCriteria,
    RawCriterionAnnotation,
    annotation_record_hash,
    import_annotation_record,
    import_blinded_assignment,
    packet_evidence_ids,
    serialize_annotation_record,
    serialize_blinded_assignment,
    validate_adjudication_sources,
    validate_annotation_record_against_packet,
    validate_raw_submission_against_assignment,
)
from judge_compare.hashing import canonical_sha256
from judge_compare.models import EvidencePacket


def _packet() -> EvidencePacket:
    return EvidencePacket.model_validate(
        {
            "schema_version": "1.0",
            "example_id": "case-opaque-1",
            "policy": "Only report results present in tool evidence.",
            "tool_definitions": [
                {
                    "name": "lookup",
                    "description": "Look up a record.",
                    "parameters": {
                        "properties": {
                            "key": {
                                "description": "Record key.",
                                "title": "Key",
                                "type": "string",
                            }
                        },
                        "required": ["key"],
                        "title": "parameters",
                        "type": "object",
                    },
                }
            ],
            "conversation_prefix": [
                {
                    "role": "user",
                    "content": "Please check my record.",
                    "turn_idx": 0,
                    "source_message_id": "msg-user",
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-lookup",
                            "name": "lookup",
                            "arguments": {"key": "record-1"},
                            "requestor": "assistant",
                        }
                    ],
                    "turn_idx": 1,
                    "source_message_id": "msg-assistant",
                },
                {
                    "role": "tool",
                    "id": "call-lookup",
                    "content": "Record found.",
                    "requestor": "assistant",
                    "error": False,
                    "turn_idx": 2,
                    "source_message_id": "msg-tool-result",
                },
            ],
            "target_response": {
                "role": "assistant",
                "content": "I found your record.",
                "turn_idx": 3,
                "source_message_id": "msg-target",
            },
        }
    )


def _raw_criterion(
    *,
    label: str = "PASS",
    evidence_ids: tuple[str, ...] = ("msg-target", "call-lookup"),
) -> RawCriterionAnnotation:
    return RawCriterionAnnotation.model_validate(
        {
            "label": label,
            "evidence_ids": evidence_ids,
            "rationale": "The target claim matches the visible lookup result.",
            "ambiguity": False,
            "elapsed_seconds": 12.5,
        }
    )


def _raw_submission(
    packet: EvidencePacket,
    *,
    suffix: str = "a",
    annotator: str = "annotator-a",
) -> RawAnnotationSubmission:
    criterion = _raw_criterion()
    return RawAnnotationSubmission(
        submission_id=f"submission-{suffix}",
        assignment_id=f"assignment-{suffix}",
        assignment_order=1,
        pool="annotation_calibration",
        example_id=packet.example_id,
        packet_hash=canonical_sha256(packet.model_dump(mode="json")),
        annotator_pseudonym=annotator,
        rubric_version="rubric-draft-1",
        rubric_hash="a" * 64,
        criteria=RawCriteria(
            grounding=criterion,
            relevance=criterion,
            policy_compliance=criterion,
        ),
    )


def _assignment(packet: EvidencePacket) -> BlindedAnnotationAssignment:
    return BlindedAnnotationAssignment(
        assignment_id="assignment-a",
        assignment_order=1,
        pool="annotation_calibration",
        example_id=packet.example_id,
        packet_hash=canonical_sha256(packet.model_dump(mode="json")),
        rubric_version="rubric-draft-1",
        rubric_hash="a" * 64,
        packet=packet,
    )


def _adjudicated_criterion() -> AdjudicatedCriterionAnnotation:
    return AdjudicatedCriterionAnnotation(
        label="PASS",
        evidence_ids=("msg-target", "call-lookup"),
        rationale="The independent records agree with the visible evidence.",
        ambiguity=False,
        elapsed_seconds=8.0,
        resolution="raw_agreement",
    )


def _adjudication(
    packet: EvidencePacket,
    submissions: tuple[RawAnnotationSubmission, ...],
) -> AdjudicatedAnnotationRecord:
    criterion = _adjudicated_criterion()
    return AdjudicatedAnnotationRecord(
        adjudication_id="adjudication-1",
        adjudication_order=1,
        pool="annotation_calibration",
        example_id=packet.example_id,
        packet_hash=canonical_sha256(packet.model_dump(mode="json")),
        adjudicator_pseudonym="adjudicator-a",
        rubric_version="rubric-draft-1",
        rubric_hash="a" * 64,
        source_submission_hashes=tuple(annotation_record_hash(item) for item in submissions),
        criteria=AdjudicatedCriteria(
            grounding=criterion,
            relevance=criterion,
            policy_compliance=criterion,
        ),
    )


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(child) for child in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(child) for child in value))
    return set()


def test_blinded_assignment_is_bound_to_packet_and_round_trips_deterministically() -> None:
    packet = _packet()
    assignment = BlindedAnnotationAssignment(
        assignment_id="assignment-a",
        assignment_order=7,
        pool="annotation_calibration",
        example_id=packet.example_id,
        packet_hash=canonical_sha256(packet.model_dump(mode="json")),
        rubric_version="rubric-draft-1",
        rubric_hash="a" * 64,
        packet=packet,
    )

    first = serialize_blinded_assignment(assignment)
    imported = import_blinded_assignment(first)

    assert serialize_blinded_assignment(imported) == first
    exported = json.loads(first)
    assert exported["policy_evidence_id"] == "policy"
    assert not (_all_keys(exported) & BLINDED_EXPORT_FORBIDDEN_KEYS)
    assert "source_message_id" in _all_keys(exported)


def test_blinded_assignment_rejects_hash_drift_and_forbidden_nested_keys() -> None:
    packet = _packet()
    common = {
        "assignment_id": "assignment-a",
        "assignment_order": 1,
        "pool": "annotation_calibration",
        "example_id": packet.example_id,
        "rubric_version": "rubric-draft-1",
        "rubric_hash": "a" * 64,
        "packet": packet,
    }
    with pytest.raises(ValidationError, match="packet_hash must match"):
        BlindedAnnotationAssignment(packet_hash="b" * 64, **common)

    packet.tool_definitions[0].parameters["split"] = "test"
    with pytest.raises(ValidationError, match="split"):
        BlindedAnnotationAssignment(
            packet_hash=canonical_sha256(packet.model_dump(mode="json")),
            **common,
        )


def test_raw_submission_has_exactly_three_typed_criteria() -> None:
    packet = _packet()
    submission = _raw_submission(packet)
    payload = submission.model_dump(mode="json")

    payload["criteria"].pop("relevance")
    with pytest.raises(ValidationError, match="relevance"):
        RawAnnotationSubmission.model_validate_json(json.dumps(payload))

    payload = submission.model_dump(mode="json")
    payload["criteria"]["overall"] = payload["criteria"]["grounding"]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RawAnnotationSubmission.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "label",
    ["PASS", "FAIL", "NOT_APPLICABLE", "UNSCORABLE"],
)
def test_all_four_annotation_labels_are_supported(label: str) -> None:
    assert _raw_criterion(label=label).label == label


def test_criterion_fields_are_strict_and_bounded() -> None:
    payload = _raw_criterion().model_dump(mode="json")
    payload["label"] = "MAYBE"
    with pytest.raises(ValidationError):
        RawCriterionAnnotation.model_validate(payload)

    payload = _raw_criterion().model_dump(mode="json")
    payload["evidence_ids"] = ["msg-target", "msg-target"]
    with pytest.raises(ValidationError, match="evidence_ids must be unique"):
        RawCriterionAnnotation.model_validate_json(json.dumps(payload))

    payload = _raw_criterion().model_dump(mode="json")
    payload["rationale"] = " "
    with pytest.raises(ValidationError, match="must not be blank"):
        RawCriterionAnnotation.model_validate_json(json.dumps(payload))

    payload = _raw_criterion().model_dump(mode="json")
    payload["elapsed_seconds"] = 0
    with pytest.raises(ValidationError):
        RawCriterionAnnotation.model_validate_json(json.dumps(payload))


def test_evidence_citations_must_exist_in_the_bound_packet() -> None:
    packet = _packet()
    assert packet_evidence_ids(packet) == {
        "policy",
        "msg-user",
        "msg-assistant",
        "call-lookup",
        "msg-tool-result",
        "msg-target",
    }
    submission = _raw_submission(packet)
    assert validate_annotation_record_against_packet(submission, packet) == submission

    invalid = submission.model_copy(
        update={
            "criteria": submission.criteria.model_copy(
                update={
                    "grounding": _raw_criterion(evidence_ids=("absent-evidence",))
                }
            )
        }
    )
    with pytest.raises(AnnotationValidationError, match="absent from the packet"):
        validate_annotation_record_against_packet(invalid, packet)


def test_raw_submission_is_deeply_immutable_and_round_trips_deterministically() -> None:
    submission = _raw_submission(_packet())
    serialized = serialize_annotation_record(submission)
    imported = import_annotation_record(serialized)

    assert isinstance(imported, RawAnnotationSubmission)
    assert serialize_annotation_record(imported) == serialized
    assert annotation_record_hash(imported) == canonical_sha256(json.loads(serialized))
    with pytest.raises(ValidationError, match="frozen"):
        submission.annotator_pseudonym = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        submission.criteria.grounding.evidence_ids[0] = "changed"  # type: ignore[index]


def test_adjudication_is_separate_and_bound_to_immutable_raw_hashes() -> None:
    packet = _packet()
    first = _raw_submission(packet, suffix="a", annotator="annotator-a")
    second = _raw_submission(packet, suffix="b", annotator="annotator-b")
    submissions = (first, second)
    adjudication = _adjudication(packet, submissions)

    assert validate_adjudication_sources(adjudication, submissions, packet) == adjudication
    serialized = serialize_annotation_record(adjudication)
    imported = import_annotation_record(serialized)
    assert isinstance(imported, AdjudicatedAnnotationRecord)
    assert serialize_annotation_record(imported) == serialized
    exported = json.loads(serialized)
    assert "source_submission_hashes" in exported
    assert "submission_id" not in _all_keys(exported)
    assert "annotator_pseudonym" not in _all_keys(exported)

    with pytest.raises(AnnotationValidationError, match="do not match"):
        validate_adjudication_sources(adjudication, tuple(reversed(submissions)), packet)


def test_unresolved_adjudication_cannot_default_to_pass() -> None:
    with pytest.raises(ValidationError, match="must be UNSCORABLE"):
        AdjudicatedCriterionAnnotation(
            label="PASS",
            evidence_ids=("msg-target",),
            rationale="Reviewers could not resolve the evidence.",
            ambiguity=True,
            elapsed_seconds=5.0,
            resolution="unresolved",
        )


@pytest.mark.parametrize(
    ("update", "field"),
    [
        ({"assignment_id": "assignment-other"}, "assignment_id"),
        ({"assignment_order": 2}, "assignment_order"),
        ({"pool": "model_judgment"}, "pool"),
        ({"example_id": "case-other"}, "example_id"),
        ({"packet_hash": "b" * 64}, "packet_hash"),
        ({"rubric_version": "rubric-other"}, "rubric_version"),
        ({"rubric_hash": "b" * 64}, "rubric_hash"),
    ],
)
def test_raw_submission_must_match_exact_blinded_assignment(
    update: dict[str, object], field: str
) -> None:
    packet = _packet()
    assignment = _assignment(packet)
    submission = _raw_submission(packet).model_copy(update=update)

    with pytest.raises(AnnotationValidationError, match=field):
        validate_raw_submission_against_assignment(submission, assignment)


def test_raw_submission_binding_revalidates_nested_model_copies() -> None:
    packet = _packet()
    assignment = _assignment(packet)
    submission = _raw_submission(packet)
    invalid_criterion = submission.criteria.grounding.model_copy(
        update={"label": "MAYBE"}
    )
    invalid_criteria = submission.criteria.model_copy(
        update={"grounding": invalid_criterion}
    )
    invalid_submission = submission.model_copy(update={"criteria": invalid_criteria})

    with pytest.raises(ValidationError):
        validate_raw_submission_against_assignment(invalid_submission, assignment)

    invalid_assignment = assignment.model_copy(update={"assignment_order": 0})
    with pytest.raises(ValidationError):
        validate_raw_submission_against_assignment(submission, invalid_assignment)


def test_raw_submission_binding_accepts_exact_revalidated_artifacts() -> None:
    packet = _packet()
    assignment = _assignment(packet)
    submission = _raw_submission(packet)

    validated = validate_raw_submission_against_assignment(submission, assignment)

    assert validated == submission
    assert validated is not submission


def test_assignment_serialization_fails_closed_after_packet_mutation() -> None:
    packet = _packet()
    assignment = _assignment(packet)
    assignment.packet.target_response.content = "A mutated answer."

    with pytest.raises(ValidationError, match="packet_hash must match"):
        serialize_blinded_assignment(assignment)

    packet = _packet()
    assignment = _assignment(packet)
    assignment.packet.tool_definitions[0].parameters["split"] = "test"
    with pytest.raises(ValidationError, match="split"):
        serialize_blinded_assignment(assignment)


def test_record_serialization_revalidates_model_copy_bypasses() -> None:
    submission = _raw_submission(_packet())
    invalid = submission.model_copy(update={"assignment_order": 0})

    with pytest.raises(ValidationError):
        serialize_annotation_record(invalid)
