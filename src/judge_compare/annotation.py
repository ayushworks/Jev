from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from judge_compare.hashing import canonical_json_bytes, canonical_sha256, sha256_bytes
from judge_compare.models import AssistantEvidence, EvidencePacket, ToolEvidence

AnnotationLabel = Literal["PASS", "FAIL", "NOT_APPLICABLE", "UNSCORABLE"]
AnnotationPool = Literal["annotation_calibration", "model_judgment"]
AdjudicationResolution = Literal[
    "raw_agreement",
    "adjudicator_decision",
    "human_consensus",
    "unresolved",
]

CRITERIA = ("grounding", "relevance", "policy_compliance")
POLICY_EVIDENCE_ID = "policy"
BLINDED_EXPORT_FORBIDDEN_KEYS = frozenset(
    {
        "agent_model",
        "evaluation_criteria",
        "evaluator_output",
        "expected_actions",
        "human_label",
        "human_labels",
        "judge",
        "judge_output",
        "judgments",
        "model",
        "model_id",
        "model_identity",
        "private_traceability",
        "provider",
        "raw_data",
        "reference_outputs",
        "returned_model_id",
        "reward",
        "reward_info",
        "source_call_id",
        "source_model",
        "source_provider_message_id",
        "source_simulation_id",
        "split",
    }
)


class AnnotationValidationError(ValueError):
    """Raised when an annotation artifact is inconsistent with its evidence."""


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


NonBlankText = Annotated[
    str,
    Field(min_length=1, max_length=200),
    AfterValidator(_nonblank),
]
ShortRationale = Annotated[
    str,
    Field(min_length=1, max_length=800),
    AfterValidator(_nonblank),
]
Sha256Text = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Pseudonym = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]
EvidenceIds = Annotated[tuple[NonBlankText, ...], Field(min_length=1)]


class FrozenStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        allow_inf_nan=False,
    )


class RawCriterionAnnotation(FrozenStrictModel):
    label: AnnotationLabel
    evidence_ids: EvidenceIds
    rationale: ShortRationale
    ambiguity: bool
    elapsed_seconds: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must be unique")
        return value


class AdjudicatedCriterionAnnotation(FrozenStrictModel):
    label: AnnotationLabel
    evidence_ids: EvidenceIds
    rationale: ShortRationale
    ambiguity: bool
    elapsed_seconds: float = Field(gt=0, allow_inf_nan=False)
    resolution: AdjudicationResolution

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must be unique")
        return value

    @model_validator(mode="after")
    def unresolved_is_unscorable(self) -> AdjudicatedCriterionAnnotation:
        if self.resolution == "unresolved" and self.label != "UNSCORABLE":
            raise ValueError("an unresolved adjudication must be UNSCORABLE")
        return self


class RawCriteria(FrozenStrictModel):
    grounding: RawCriterionAnnotation
    relevance: RawCriterionAnnotation
    policy_compliance: RawCriterionAnnotation


class AdjudicatedCriteria(FrozenStrictModel):
    grounding: AdjudicatedCriterionAnnotation
    relevance: AdjudicatedCriterionAnnotation
    policy_compliance: AdjudicatedCriterionAnnotation


class BlindedAnnotationAssignment(FrozenStrictModel):
    artifact_type: Literal["blinded_annotation_assignment"] = (
        "blinded_annotation_assignment"
    )
    schema_version: Literal["1.0"] = "1.0"
    assignment_id: NonBlankText
    assignment_order: int = Field(ge=1)
    pool: AnnotationPool
    example_id: NonBlankText
    packet_hash: Sha256Text
    rubric_version: NonBlankText
    rubric_hash: Sha256Text
    policy_evidence_id: Literal["policy"] = POLICY_EVIDENCE_ID
    packet: EvidencePacket

    @model_validator(mode="after")
    def validate_blinded_packet(self) -> BlindedAnnotationAssignment:
        if self.example_id != self.packet.example_id:
            raise ValueError("assignment example_id must match the packet")
        actual_hash = canonical_sha256(self.packet.model_dump(mode="json"))
        if self.packet_hash != actual_hash:
            raise ValueError("assignment packet_hash must match the packet")
        forbidden = _find_forbidden_keys(self.model_dump(mode="json"))
        if forbidden:
            raise ValueError(
                "blinded annotation export contains forbidden keys: "
                f"{', '.join(sorted(forbidden))}"
            )
        return self


class RawAnnotationSubmission(FrozenStrictModel):
    record_type: Literal["raw_annotation_submission"] = "raw_annotation_submission"
    schema_version: Literal["1.0"] = "1.0"
    submission_id: NonBlankText
    assignment_id: NonBlankText
    assignment_order: int = Field(ge=1)
    pool: AnnotationPool
    example_id: NonBlankText
    packet_hash: Sha256Text
    annotator_pseudonym: Pseudonym
    rubric_version: NonBlankText
    rubric_hash: Sha256Text
    criteria: RawCriteria


class AdjudicatedAnnotationRecord(FrozenStrictModel):
    record_type: Literal["adjudicated_annotation_record"] = (
        "adjudicated_annotation_record"
    )
    schema_version: Literal["1.0"] = "1.0"
    adjudication_id: NonBlankText
    adjudication_order: int = Field(ge=1)
    pool: AnnotationPool
    example_id: NonBlankText
    packet_hash: Sha256Text
    adjudicator_pseudonym: Pseudonym
    rubric_version: NonBlankText
    rubric_hash: Sha256Text
    source_submission_hashes: Annotated[tuple[Sha256Text, ...], Field(min_length=2)]
    criteria: AdjudicatedCriteria

    @field_validator("source_submission_hashes")
    @classmethod
    def source_hashes_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("source_submission_hashes must be unique")
        return value


AnnotationRecord = Annotated[
    RawAnnotationSubmission | AdjudicatedAnnotationRecord,
    Field(discriminator="record_type"),
]
_ANNOTATION_RECORD_ADAPTER = TypeAdapter(AnnotationRecord)


def packet_evidence_ids(packet: EvidencePacket) -> frozenset[str]:
    """Return IDs visible in a blinded packet and therefore valid for citation."""
    evidence_ids = {POLICY_EVIDENCE_ID}
    for message in packet.conversation_prefix:
        if message.source_message_id is not None:
            evidence_ids.add(message.source_message_id)
        if isinstance(message, AssistantEvidence):
            evidence_ids.update(call.id for call in message.tool_calls or ())
        elif isinstance(message, ToolEvidence):
            evidence_ids.add(message.id)
    if packet.target_response.source_message_id is not None:
        evidence_ids.add(packet.target_response.source_message_id)
    return frozenset(evidence_ids)


def validate_annotation_record_against_packet(
    record: RawAnnotationSubmission | AdjudicatedAnnotationRecord,
    packet: EvidencePacket,
) -> RawAnnotationSubmission | AdjudicatedAnnotationRecord:
    """Bind one immutable record to a packet and validate every evidence citation."""
    validated_record = _revalidate_annotation_record(record)
    validated_packet = _revalidate_packet(packet)
    _validate_record_against_packet(validated_record, validated_packet)
    return validated_record


def validate_raw_submission_against_assignment(
    submission: RawAnnotationSubmission,
    assignment: BlindedAnnotationAssignment,
) -> RawAnnotationSubmission:
    """Revalidate and bind a raw submission to its exact blinded assignment."""
    validated_submission = _revalidate_annotation_record(submission)
    if not isinstance(validated_submission, RawAnnotationSubmission):
        raise TypeError("submission must be a RawAnnotationSubmission")
    validated_assignment = _revalidate_blinded_assignment(assignment)

    binding_fields = (
        "assignment_id",
        "assignment_order",
        "pool",
        "example_id",
        "packet_hash",
        "rubric_version",
        "rubric_hash",
    )
    mismatches = [
        field
        for field in binding_fields
        if getattr(validated_submission, field) != getattr(validated_assignment, field)
    ]
    if mismatches:
        raise AnnotationValidationError(
            "raw submission does not match its blinded assignment: "
            f"{', '.join(mismatches)}"
        )
    _validate_record_against_packet(validated_submission, validated_assignment.packet)
    return validated_submission


def _validate_record_against_packet(
    record: RawAnnotationSubmission | AdjudicatedAnnotationRecord,
    packet: EvidencePacket,
) -> None:
    if record.example_id != packet.example_id:
        raise AnnotationValidationError("annotation example_id does not match the packet")
    packet_hash = canonical_sha256(packet.model_dump(mode="json"))
    if record.packet_hash != packet_hash:
        raise AnnotationValidationError("annotation packet_hash does not match the packet")

    available = packet_evidence_ids(packet)
    for criterion in CRITERIA:
        annotation = getattr(record.criteria, criterion)
        missing = set(annotation.evidence_ids) - available
        if missing:
            raise AnnotationValidationError(
                f"{criterion} cites evidence IDs absent from the packet: {sorted(missing)}"
            )


def annotation_record_hash(
    record: RawAnnotationSubmission | AdjudicatedAnnotationRecord,
) -> str:
    return sha256_bytes(serialize_annotation_record(record))


def validate_adjudication_sources(
    adjudication: AdjudicatedAnnotationRecord,
    raw_submissions: tuple[RawAnnotationSubmission, ...],
    packet: EvidencePacket,
) -> AdjudicatedAnnotationRecord:
    """Validate an adjudication without mutating or embedding either raw submission."""
    validated_adjudication = validate_annotation_record_against_packet(adjudication, packet)
    if not isinstance(validated_adjudication, AdjudicatedAnnotationRecord):
        raise TypeError("adjudication must be an AdjudicatedAnnotationRecord")
    validated_submissions: list[RawAnnotationSubmission] = []
    for submission in raw_submissions:
        validated = validate_annotation_record_against_packet(submission, packet)
        if not isinstance(validated, RawAnnotationSubmission):
            raise TypeError("raw_submissions must contain only RawAnnotationSubmission values")
        validated_submissions.append(validated)
    if len(raw_submissions) < 2:
        raise AnnotationValidationError("adjudication requires at least two raw submissions")
    observed_hashes = tuple(annotation_record_hash(item) for item in validated_submissions)
    if validated_adjudication.source_submission_hashes != observed_hashes:
        raise AnnotationValidationError(
            "adjudication source_submission_hashes do not match the supplied raw submissions"
        )
    if len({item.annotator_pseudonym for item in validated_submissions}) != len(
        validated_submissions
    ):
        raise AnnotationValidationError("raw submissions must use distinct annotators")

    bindings = (
        "pool",
        "example_id",
        "packet_hash",
        "rubric_version",
        "rubric_hash",
    )
    for submission in validated_submissions:
        if any(
            getattr(submission, field) != getattr(validated_adjudication, field)
            for field in bindings
        ):
            raise AnnotationValidationError(
                "raw submission bindings do not match the adjudication"
            )
    return validated_adjudication


def serialize_annotation_record(
    record: RawAnnotationSubmission | AdjudicatedAnnotationRecord,
) -> bytes:
    """Serialize a record canonically so identical records have identical bytes."""
    if not isinstance(record, (RawAnnotationSubmission, AdjudicatedAnnotationRecord)):
        raise TypeError("record must be a raw submission or adjudicated record")
    validated = _revalidate_annotation_record(record)
    return canonical_json_bytes(validated.model_dump(mode="json"))


def import_annotation_record(payload: str | bytes) -> AnnotationRecord:
    """Import one strict record from its deterministic JSON representation."""
    return _ANNOTATION_RECORD_ADAPTER.validate_json(payload)


def serialize_blinded_assignment(assignment: BlindedAnnotationAssignment) -> bytes:
    """Serialize a validated blinded assignment canonically."""
    if not isinstance(assignment, BlindedAnnotationAssignment):
        raise TypeError("assignment must be a BlindedAnnotationAssignment")
    validated = _revalidate_blinded_assignment(assignment)
    return canonical_json_bytes(validated.model_dump(mode="json"))


def import_blinded_assignment(payload: str | bytes) -> BlindedAnnotationAssignment:
    """Import and revalidate one strict blinded assignment."""
    return BlindedAnnotationAssignment.model_validate_json(payload)


def _revalidate_packet(packet: EvidencePacket) -> EvidencePacket:
    if not isinstance(packet, EvidencePacket):
        raise TypeError("packet must be an EvidencePacket")
    payload = canonical_json_bytes(packet.model_dump(mode="json"))
    return EvidencePacket.model_validate_json(payload)


def _revalidate_annotation_record(
    record: RawAnnotationSubmission | AdjudicatedAnnotationRecord,
) -> RawAnnotationSubmission | AdjudicatedAnnotationRecord:
    if not isinstance(record, (RawAnnotationSubmission, AdjudicatedAnnotationRecord)):
        raise TypeError("record must be a raw submission or adjudicated record")
    payload = canonical_json_bytes(record.model_dump(mode="json"))
    return _ANNOTATION_RECORD_ADAPTER.validate_json(payload)


def _revalidate_blinded_assignment(
    assignment: BlindedAnnotationAssignment,
) -> BlindedAnnotationAssignment:
    if not isinstance(assignment, BlindedAnnotationAssignment):
        raise TypeError("assignment must be a BlindedAnnotationAssignment")
    payload = canonical_json_bytes(assignment.model_dump(mode="json"))
    return BlindedAnnotationAssignment.model_validate_json(payload)


def _find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.lower()
            if lowered in BLINDED_EXPORT_FORBIDDEN_KEYS or lowered.endswith("_model_id"):
                found.add(key)
            found.update(_find_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_find_forbidden_keys(child))
    return found
