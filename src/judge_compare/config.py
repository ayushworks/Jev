from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

PENDING_VALUE = "pending_g0"
Pending = Literal["pending_g0"]
PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
NonnegativeFloat = Annotated[float, Field(ge=0)]
ReductionFactor = Annotated[float, Field(gt=1)]
UnitFloat = Annotated[float, Field(ge=0, le=1)]
PercentPoints = Annotated[float, Field(ge=0, le=100)]
Sha40 = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
SafeSlug = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
JsonFilename = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")]
UuidText = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class UpstreamConfig(StrictModel):
    repository: Literal["https://github.com/danielgshea/jev-as-a-judge"]
    commit: Sha40
    experiment_id: UuidText
    audit_mode: Literal["archived_no_model_calls"]


class BenchmarkConfig(StrictModel):
    repository: Literal["https://github.com/sierra-research/tau2-bench"]
    commit: Sha40
    version: Literal["0.2.1-dev"]
    domain: Literal["retail"]
    source_submission: SafeSlug
    source_trajectory_file: JsonFilename
    source_agent_model: Literal["anthropic/claude-sonnet-4-5-20250929"]
    source_simulator_model: Literal["gpt-5.2"]
    source_run_commit: Sha40
    source_run_revision_verified: bool
    source_run_revision_disposition: Literal[
        "pending_g0",
        "accepted_compatibility_pin_with_unresolved_generation_sha",
        "exact_revision_verified",
    ]

    @model_validator(mode="after")
    def revision_status_is_consistent(self) -> BenchmarkConfig:
        if self.source_run_revision_verified and self.source_run_revision_disposition == (
            "accepted_compatibility_pin_with_unresolved_generation_sha"
        ):
            raise ValueError(
                "a verified source revision cannot use the provenance-gap disposition"
            )
        if (
            not self.source_run_revision_verified
            and self.source_run_revision_disposition == "exact_revision_verified"
        ):
            raise ValueError("an unverified source revision cannot use exact_revision_verified")
        return self


class SplitConfig(StrictModel):
    group_key: Literal["task_family_id"]
    development_fraction: float = Field(gt=0, lt=1)
    seed: int


class JevConfig(StrictModel):
    model: Literal["jev-1.13.0"]
    sdk_version: Literal["typesafe-sdk==0.7.0", "pending_g0"]


class ReasoningSettings(StrictModel):
    effort: Literal["medium"]


class SamplingSettings(StrictModel):
    temperature: Literal["not_applicable"]
    top_p: Literal["not_applicable"]
    seed: Literal["not_applicable"]


class ResponseSettings(StrictModel):
    structured_output: Literal[True]
    tools: list[str]
    store: Literal[False]
    service_tier: Literal["default"]
    truncation: Literal["disabled"]

    @model_validator(mode="after")
    def tools_are_disabled(self) -> ResponseSettings:
        if self.tools:
            raise ValueError("frontier judge tools must be disabled")
        return self


class FrontierConfig(StrictModel):
    provider: Literal["openai", "pending_g0"]
    model: Literal["gpt-6-astra", "gpt-5.4-2026-03-05", "pending_g0"]
    sdk_version: Literal["openai==3.16.2", "pending_g0"]
    api: Literal["responses", "pending_g0"]
    reasoning_setting: Pending | ReasoningSettings
    sampling_settings: Pending | SamplingSettings
    response_settings: Pending | ResponseSettings
    max_input_tokens: PositiveInt | Pending
    max_output_tokens: PositiveInt | Pending


class JudgesConfig(StrictModel):
    jev: JevConfig
    frontier: FrontierConfig


class ExecutionConfig(StrictModel):
    primary_concurrency: int = Field(ge=1)
    scheduling_seed: int
    timeout_seconds: PositiveInt | Pending
    maximum_attempts: int = Field(ge=1, le=3)
    maximum_cases: PositiveInt | Pending
    maximum_paid_requests: PositiveInt | Pending
    budget_usd: PositiveFloat | Pending
    price_snapshot_date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class RepeatabilityConfig(StrictModel):
    enabled: bool
    target_packets: int = Field(ge=0)
    max_packets_per_task_family: int = Field(ge=1)
    total_judgments_per_packet_per_judge: int = Field(ge=1)
    primary_repetition_id: Literal[0]
    selection_seed: int
    selection_basis: Literal["task_family_and_packet_length_without_labels"]
    additional_budget_usd: NonnegativeFloat | Pending
    maximum_share_of_total_judging_budget: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def disabled_study_has_no_hidden_work(self) -> RepeatabilityConfig:
        if self.enabled:
            if self.target_packets < 1:
                raise ValueError("enabled repeatability requires at least one packet")
            if self.total_judgments_per_packet_per_judge < 2:
                raise ValueError(
                    "enabled repeatability requires at least two judgments per judge"
                )
            if isinstance(self.additional_budget_usd, float) and (
                self.additional_budget_usd <= 0
            ):
                raise ValueError("enabled repeatability requires a positive budget")
        elif (
            self.target_packets != 0
            or self.total_judgments_per_packet_per_judge != 1
            or self.additional_budget_usd != 0.0
        ):
            raise ValueError(
                "disabled repeatability requires zero packets, one primary judgment, "
                "and zero additional budget"
            )
        return self


class RecordingConfig(StrictModel):
    local_artifacts_required: Literal[True]
    langsmith_enabled: bool


class AnalysisConfig(StrictModel):
    primary_criterion: Literal["grounding"]
    primary_population: Literal["paired_valid_human_scorable"]
    primary_threshold_analysis: Literal["fixed"]
    fixed_failure_threshold: float = Field(ge=0, le=1)
    target_missed_failure_rate: float = Field(ge=0, le=1)
    acceptable_fnr_increase_pp: PercentPoints | Pending
    acceptable_fpr_increase_pp: PercentPoints | Pending
    minimum_cost_reduction_factor: ReductionFactor | Pending
    minimum_p95_latency_reduction_factor: ReductionFactor | Pending
    confidence_level: float = Field(gt=0, lt=1)
    cluster_bootstrap_replicates: int = Field(ge=1_000)
    minimum_decision_coverage: UnitFloat | Pending
    acceptable_all_cases_missed_failure_increase_pp: PercentPoints | Pending


class ExperimentConfig(StrictModel):
    experiment_id: SafeSlug
    protocol_revision: Literal[2]
    upstream: UpstreamConfig
    benchmark: BenchmarkConfig
    unit: Literal["final_customer_facing_response"]
    split: SplitConfig
    judges: JudgesConfig
    execution: ExecutionConfig
    repeatability: RepeatabilityConfig
    recording: RecordingConfig
    analysis: AnalysisConfig

    @model_validator(mode="after")
    def validate_no_nulls(self) -> ExperimentConfig:
        if _find_null_paths(self.model_dump()):
            raise ValueError("configuration must use explicit pending values, not null")
        return self

    def pending_review_paths(self) -> list[str]:
        return _find_value_paths(self.model_dump(), PENDING_VALUE)

    def assert_g0_resolved(self) -> None:
        pending = self.pending_review_paths()
        if pending:
            joined = ", ".join(pending)
            raise ValueError(f"G0 configuration is unresolved: {joined}")


def _find_null_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if value is None:
        return [prefix or "<root>"]
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_find_null_paths(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_find_null_paths(child, f"{prefix}[{index}]"))
    return paths


def _find_value_paths(value: Any, target: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if value == target:
        return [prefix or "<root>"]
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_find_value_paths(child, target, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_find_value_paths(child, target, f"{prefix}[{index}]"))
    return paths


def load_config(path: Path) -> ExperimentConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return ExperimentConfig.model_validate(raw)
