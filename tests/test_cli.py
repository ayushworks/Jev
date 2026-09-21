from pathlib import Path

from typer.testing import CliRunner

from judge_compare.cli import app


def test_offline_audit_requires_explicit_no_model_calls_flag() -> None:
    result = CliRunner().invoke(app, ["audit-upstream"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_g0_packaging_requires_explicit_no_model_calls_flag() -> None:
    result = CliRunner().invoke(app, ["prepare-g0"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_m1_preparation_requires_explicit_no_model_calls_flag() -> None:
    result = CliRunner().invoke(app, ["prepare-m1"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_m1_normalization_audit_preparation_requires_explicit_offline_flag() -> None:
    result = CliRunner().invoke(app, ["prepare-m1-normalization-audit"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_m1_normalization_review_page_requires_explicit_offline_flag() -> None:
    result = CliRunner().invoke(app, ["prepare-m1-normalization-review-page"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_m1_request_fit_preparation_requires_explicit_offline_flag() -> None:
    result = CliRunner().invoke(app, ["prepare-m1-request-fit"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_rubric_development_preparation_requires_explicit_offline_flag() -> None:
    result = CliRunner().invoke(app, ["prepare-rubric-development"])

    assert result.exit_code != 0
    assert "explicitly pass --no-model-calls" in result.output


def test_g0_package_is_frozen_after_approval() -> None:
    result = CliRunner().invoke(app, ["prepare-g0", "--no-model-calls"])

    assert result.exit_code != 0
    assert "G0 is already approved" in result.output


def test_m0_audit_outputs_are_frozen_after_g0_approval() -> None:
    result = CliRunner().invoke(app, ["m0-audit", "--no-model-calls"])

    assert result.exit_code != 0
    assert "M0 audit outputs are frozen after G0 approval" in result.output


def test_approved_decision_cannot_be_bypassed_with_another_config(
    tmp_path: Path,
) -> None:
    pending_config = tmp_path / "pending.yaml"
    pending_config.write_text("this: is-not-even-loaded\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "audit-source",
            "--no-model-calls",
            "--config",
            str(pending_config),
        ],
    )

    assert result.exit_code != 0
    assert "M0 audit outputs are frozen after G0 approval" in result.output
