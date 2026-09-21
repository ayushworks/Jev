from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import yaml

from judge_compare.benchmark_reward_run import BenchmarkRunError
from judge_compare.benchmark_reward_run import run as run_benchmark_reward
from judge_compare.config import load_config
from judge_compare.m1 import (
    M1PreparationError,
    prepare_m1,
    record_m1_normalization_audit,
)
from judge_compare.multisignal_run import projected_cost as projected_multisignal_cost
from judge_compare.multisignal_run import run as run_multisignal
from judge_compare.normalization_audit import (
    NormalizationAuditPreparationError,
    prepare_m1_normalization_audit,
)
from judge_compare.normalization_review_page import (
    NormalizationReviewPageError,
    prepare_normalization_review_page,
)
from judge_compare.packet_builder import PacketBuildError
from judge_compare.paths import project_root
from judge_compare.request_fit_batch import (
    M1RequestFitPreparationError,
    prepare_m1_request_fit,
)
from judge_compare.review import prepare_g0_manifest
from judge_compare.rubric_development import (
    RubricDevelopmentPreparationError,
    prepare_single_reviewer_rubric_development,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Build and audit the Jev/frontier customer-support judge comparison.",
)
DEFAULT_CONFIG = project_root() / "configs" / "experiment.yaml"


@app.command("plan-multisignal-comparison")
def plan_multisignal_comparison_command(
    repetitions: Annotated[int, typer.Option("--repetitions", min=1)] = 100,
) -> None:
    """Estimate the repeated five-signal comparison from measured prior usage."""
    projection = projected_multisignal_cost(repetitions)
    typer.echo(
        f"{repetitions} repetitions: prior-usage projection "
        f"${projection['prior_run_usage_projection_usd']:.2f}; "
        "20% buffered projection "
        f"${projection['prior_run_usage_projection_with_20_percent_buffer_usd']:.2f}"
    )


@app.command("run-multisignal-comparison")
def run_multisignal_comparison_command(
    repetitions: Annotated[int, typer.Option("--repetitions", min=1)] = 100,
    budget_usd: Annotated[float, typer.Option("--budget-usd", min=0.01)] = 30.0,
    workers: Annotated[int, typer.Option("--workers", min=1, max=12)] = 1,
    authorize_paid_calls: Annotated[bool, typer.Option("--authorize-paid-calls")] = False,
) -> None:
    """Run repeated, prompt-aligned Jev/GPT five-signal evaluation."""
    if not authorize_paid_calls:
        raise typer.BadParameter("explicitly pass --authorize-paid-calls")
    try:
        result = run_multisignal(
            project_root(), repetitions=repetitions, budget_usd=budget_usd, workers=workers
        )
    except (BenchmarkRunError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"multi-signal comparison complete: {repetitions} repetitions, "
        f"cost ${result['metadata']['observed_cost_usd']:.4f}"
    )


@app.command("run-benchmark-reward-comparison")
def run_benchmark_reward_comparison_command(
    authorize_paid_calls: Annotated[bool, typer.Option("--authorize-paid-calls")] = False,
) -> None:
    """Run the 21-case Jev/GPT comparison against the tau2 binary reward."""
    if not authorize_paid_calls:
        raise typer.BadParameter("explicitly pass --authorize-paid-calls")
    try:
        result = run_benchmark_reward(project_root())
    except (BenchmarkRunError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    metrics = result["metrics"]
    typer.echo(
        "comparison complete: "
        f"Jev accuracy {metrics['jev']['accuracy']:.1%}, "
        f"GPT-5.4 accuracy {metrics['gpt']['accuracy']:.1%}, "
        f"cost ${result['metadata']['observed_cost_usd']:.4f}"
    )


def _require_offline_flag(no_model_calls: bool) -> None:
    if not no_model_calls:
        raise typer.BadParameter(
            "explicitly pass --no-model-calls; offline preparation must never call a model"
        )


def _require_g0_pending(config_path: Path) -> None:
    decision_path = project_root() / "reviews" / "G0-decision.yaml"
    if decision_path.is_file():
        with decision_path.open("r", encoding="utf-8") as handle:
            decision = yaml.safe_load(handle)
        if isinstance(decision, dict) and decision.get("decision") == "approved":
            raise typer.BadParameter(
                "M0 audit outputs are frozen after G0 approval; do not overwrite them"
            )
    config = load_config(config_path)
    if not config.pending_review_paths():
        raise typer.BadParameter("M0 audit commands require an unresolved G0 configuration")


@app.command("validate-config")
def validate_config(
    config_path: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, readable=True),
    ] = DEFAULT_CONFIG,
    require_g0_resolved: Annotated[
        bool,
        typer.Option("--require-g0-resolved", help="Fail if G0 review fields are pending."),
    ] = False,
) -> None:
    """Validate the typed experiment configuration."""
    config = load_config(config_path)
    pending = config.pending_review_paths()
    if require_g0_resolved:
        config.assert_g0_resolved()
    typer.echo(f"valid configuration: {config.experiment_id} (protocol revision 2)")
    if pending:
        typer.echo("pending G0 fields:")
        for path in pending:
            typer.echo(f"- {path}")


@app.command("audit-upstream")
def audit_upstream_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
    config_path: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, readable=True),
    ] = DEFAULT_CONFIG,
) -> None:
    """Download and audit only the pinned upstream archive."""
    from judge_compare.upstream_audit import audit_upstream

    _require_offline_flag(no_model_calls)
    _require_g0_pending(config_path)
    root = project_root()
    config = load_config(config_path)
    manifest = audit_upstream(
        config,
        archive_root=root / "data" / "upstream_archive",
        manifest_path=root / "data" / "manifests" / "upstream_archive_manifest.json",
        report_path=root / "reports" / "upstream-audit.md",
        reuse_path=root / "protocol" / "upstream-reuse.md",
    )
    typer.echo(
        f"audited {len(manifest['artifacts'])} pinned artifacts; "
        "no model clients were initialized"
    )


@app.command("audit-source")
def audit_source_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
    config_path: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, readable=True),
    ] = DEFAULT_CONFIG,
) -> None:
    """Download and inspect the proposed public support corpus."""
    from judge_compare.source_audit import audit_support_source

    _require_offline_flag(no_model_calls)
    _require_g0_pending(config_path)
    root = project_root()
    config = load_config(config_path)
    manifest = audit_support_source(
        config,
        raw_root=root / "data" / "raw",
        manifest_path=root / "data" / "manifests" / "candidate_source_manifest.json",
        sample_path=root / "data" / "manifests" / "candidate_source_sample.json",
        report_path=root / "reports" / "candidate-source-audit.md",
    )
    count = manifest["findings"]["simulation_count"]
    typer.echo(f"audited {count} archived support trajectories; no model calls were made")


@app.command("m0-audit")
def m0_audit_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
    config_path: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, readable=True),
    ] = DEFAULT_CONFIG,
) -> None:
    """Run both offline M0 archive/source audits."""
    from judge_compare.source_audit import audit_support_source
    from judge_compare.upstream_audit import audit_upstream

    _require_offline_flag(no_model_calls)
    _require_g0_pending(config_path)
    root = project_root()
    config = load_config(config_path)
    upstream = audit_upstream(
        config,
        archive_root=root / "data" / "upstream_archive",
        manifest_path=root / "data" / "manifests" / "upstream_archive_manifest.json",
        report_path=root / "reports" / "upstream-audit.md",
        reuse_path=root / "protocol" / "upstream-reuse.md",
    )
    source = audit_support_source(
        config,
        raw_root=root / "data" / "raw",
        manifest_path=root / "data" / "manifests" / "candidate_source_manifest.json",
        sample_path=root / "data" / "manifests" / "candidate_source_sample.json",
        report_path=root / "reports" / "candidate-source-audit.md",
    )
    typer.echo(
        "M0 offline audit complete: "
        f"{len(upstream['artifacts'])} upstream artifacts, "
        f"{source['findings']['simulation_count']} support trajectories, zero model calls"
    )


@app.command("prepare-g0")
def prepare_g0_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Hash the validated M0 artifacts into the pending G0 review package."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        manifest = prepare_g0_manifest(root, root / "reviews" / "G0-package.json")
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"prepared {len(manifest['artifacts'])} G0 artifacts; "
        "owner decision pending, zero model calls"
    )


@app.command("prepare-m1")
def prepare_m1_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Build local M1 packets and provisional pools without provider calls."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        result = prepare_m1(root)
    except (M1PreparationError, PacketBuildError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    corpus_counts = result.corpus_manifest["counts"]
    pool_counts = result.pool_manifest["counts"]
    typer.echo(
        "M1 offline preparation proposal written: "
        f"{corpus_counts['canonical_packets']} canonical packets, "
        f"{pool_counts['model_judgment_cases']} provisional model cases, "
        f"{pool_counts['annotation_calibration_cases']} provisional calibration cases, "
        "zero network/model/token-count calls"
    )
    typer.echo(
        "immutable proposal records exact GPT-5.4/Jev request-fit validation and "
        "the signed 10-packet human source-normalization audit as open at proposal "
        "time; later resolution artifacts are separate, and neither M1 nor the "
        "selected pools are final or frozen"
    )


@app.command("record-m1-normalization-audit")
def record_m1_normalization_audit_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Append an approved human packet-normalization audit resolution."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        result = record_m1_normalization_audit(root)
    except (M1PreparationError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        "recorded append-only M1 normalization-audit resolution for "
        f"{result.manifest['review']['packets_reviewed']} packets; "
        "exact context eligibility remains open and M1 is not final"
    )


@app.command("prepare-m1-normalization-audit")
def prepare_m1_normalization_audit_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Prepare the private ten-case normalization review without approving it."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        result = prepare_m1_normalization_audit(root)
    except (NormalizationAuditPreparationError, PacketBuildError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        "prepared 10 private M1 normalization-audit cases and worksheet; "
        "human review remains pending, measured pools are unchanged, and zero "
        "network/model/token-count calls were made"
    )
    typer.echo(f"worksheet: {result.worksheet_path}")


@app.command("prepare-m1-normalization-review-page")
def prepare_m1_normalization_review_page_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Build the private, self-contained browser page for the ten-case audit."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        result = prepare_normalization_review_page(root)
    except (NormalizationReviewPageError, PacketBuildError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"prepared a private local review page for {result.case_count} cases; "
        "zero network/model calls were made"
    )
    typer.echo(f"page: {result.path}")


@app.command("prepare-rubric-development")
def prepare_rubric_development_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Prepare Ayush's private 24-case rubric-development worksheet offline."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        result = prepare_single_reviewer_rubric_development(root)
    except (RubricDevelopmentPreparationError, PacketBuildError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        "prepared 24 private, unlabeled rubric-development assignments for "
        "Ayush; no study cases were exposed and zero network/model calls were made"
    )
    typer.echo(f"worksheet: {result.worksheet_path}")


@app.command("prepare-m1-request-fit")
def prepare_m1_request_fit_command(
    no_model_calls: Annotated[bool, typer.Option("--no-model-calls")] = False,
) -> None:
    """Freeze private provider request envelopes without counting or judging."""
    _require_offline_flag(no_model_calls)
    root = project_root()
    try:
        result = prepare_m1_request_fit(root)
    except (M1RequestFitPreparationError, M1PreparationError, PacketBuildError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        "prepared "
        f"{result.manifest['counts']['request_envelopes']} private M1 request "
        "envelopes; exact counts remain pending and zero "
        "network/provider/model/token-count calls were made"
    )
    typer.echo(f"private manifest: {result.manifest_path}")
