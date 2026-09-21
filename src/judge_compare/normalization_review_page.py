from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from judge_compare.hashing import canonical_sha256, sha256_bytes, sha256_file
from judge_compare.io import write_text_atomic
from judge_compare.m1 import HUMAN_AUDIT_REQUIRED_CHECKS, PACKET_ROOT_RELATIVE
from judge_compare.models import EvidencePacket
from judge_compare.normalization_audit import (
    AUDIT_CASE_COUNT,
    AUDIT_ROOT_NAME,
    NormalizationAuditPreparationError,
    prepare_m1_normalization_audit,
)
from judge_compare.packet_builder import PINNED_RETAIL_TOOL_DEFINITIONS_SHA256
from judge_compare.paths import safe_join

REVIEW_PAGE_NAME = "M1-normalization-audit-review.html"
REVIEW_PAGE_SCHEMA_VERSION = "1.0"


class NormalizationReviewPageError(NormalizationAuditPreparationError):
    """Raised when the private normalization-review page cannot be built safely."""


@dataclass(frozen=True, slots=True)
class NormalizationReviewPage:
    path: Path
    source_review_aid_sha256: str
    page_sha256: str
    case_count: int


def prepare_normalization_review_page(root: Path) -> NormalizationReviewPage:
    """Build a self-contained, private browser UI for the ten-case audit."""

    resolved_root = root.resolve()
    prepared = prepare_m1_normalization_audit(resolved_root)
    review_aid_path = prepared.review_aid_path.resolve()
    review_aid_bytes = review_aid_path.read_bytes()
    review_aid_sha256 = sha256_bytes(review_aid_bytes)
    try:
        review_aid = json.loads(review_aid_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NormalizationReviewPageError(
            f"could not decode the normalization review aid: {error}"
        ) from error
    _validate_review_aid(review_aid)
    if review_aid != prepared.review_aid:
        raise NormalizationReviewPageError(
            "normalization review aid changed after offline preparation"
        )

    output_path = safe_join(
        resolved_root,
        *PACKET_ROOT_RELATIVE,
        AUDIT_ROOT_NAME,
        REVIEW_PAGE_NAME,
    )
    _assert_private_output(resolved_root, output_path)
    page = _render_review_page(
        review_aid,
        review_aid_sha256=review_aid_sha256,
    )
    if output_path.is_symlink() or (output_path.exists() and not output_path.is_file()):
        raise NormalizationReviewPageError(
            "normalization review page path must be a regular file"
        )
    if not output_path.exists() or output_path.read_text(encoding="utf-8") != page:
        write_text_atomic(output_path, page)
    return NormalizationReviewPage(
        path=output_path,
        source_review_aid_sha256=review_aid_sha256,
        page_sha256=sha256_file(output_path),
        case_count=len(review_aid["cases"]),
    )


def _validate_review_aid(value: Any) -> None:
    if not isinstance(value, dict):
        raise NormalizationReviewPageError("normalization review aid must be an object")
    expected_metadata = {
        "schema_version": REVIEW_PAGE_SCHEMA_VERSION,
        "artifact_type": "private_m1_normalization_audit_review_aid",
        "status": "prepared_for_human_review_not_reviewed_or_approved",
        "privacy": "private_gitignored_contains_source_content_rewards_and_ids",
        "model_calls_made": 0,
        "network_access_used": False,
        "human_signoff_present": False,
    }
    mismatches = {
        key: {"expected": expected, "observed": value.get(key)}
        for key, expected in expected_metadata.items()
        if value.get(key) != expected
    }
    if mismatches:
        raise NormalizationReviewPageError(
            f"normalization review aid metadata differs: {mismatches}"
        )
    if value.get("required_checks") != list(HUMAN_AUDIT_REQUIRED_CHECKS):
        raise NormalizationReviewPageError(
            "normalization review aid check definitions differ"
        )
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != AUDIT_CASE_COUNT:
        raise NormalizationReviewPageError(
            f"normalization review aid must contain exactly {AUDIT_CASE_COUNT} cases"
        )
    observed_ids: set[str] = set()
    for expected_number, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise NormalizationReviewPageError("normalization review case is malformed")
        example_id = case.get("example_id")
        review = case.get("review")
        checks = review.get("checks") if isinstance(review, dict) else None
        if (
            case.get("case_number") != expected_number
            or not isinstance(example_id, str)
            or not example_id
            or example_id in observed_ids
            or set(checks or {}) != set(HUMAN_AUDIT_REQUIRED_CHECKS)
            or any(result is not None for result in (checks or {}).values())
            or not isinstance(case.get("original_source_simulation"), dict)
            or not isinstance(case.get("canonical_packet"), dict)
        ):
            raise NormalizationReviewPageError(
                f"normalization review case {expected_number} is not a pristine assignment"
            )
        EvidencePacket.model_validate(case["canonical_packet"])
        observed_ids.add(example_id)


def _assert_private_output(root: Path, output_path: Path) -> None:
    expected = safe_join(
        root,
        *PACKET_ROOT_RELATIVE,
        AUDIT_ROOT_NAME,
        REVIEW_PAGE_NAME,
    )
    if output_path.resolve() != expected:
        raise NormalizationReviewPageError(
            "normalization review page may be written only to its private audit path"
        )
    relative = output_path.relative_to(root).as_posix()
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if ignored.returncode != 0:
        raise NormalizationReviewPageError(
            "normalization review page must remain covered by git ignore rules"
        )
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--error-unmatch", "--", relative],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if tracked.returncode == 0:
        raise NormalizationReviewPageError(
            "normalization review page must not be tracked by git"
        )


def _render_review_page(
    review_aid: dict[str, Any],
    *,
    review_aid_sha256: str,
) -> str:
    definitions = review_aid["cases"][0]["canonical_packet"]["tool_definitions"]
    definitions_hash = canonical_sha256(definitions)
    if definitions_hash != PINNED_RETAIL_TOOL_DEFINITIONS_SHA256:
        raise NormalizationReviewPageError(
            "normalization review tool definitions do not match the pinned source"
        )
    if any(
        canonical_sha256(case["canonical_packet"]["tool_definitions"])
        != definitions_hash
        for case in review_aid["cases"]
    ):
        raise NormalizationReviewPageError(
            "normalization review cases do not share the pinned tool definitions"
        )
    compact = json.dumps(
        review_aid,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.b64encode(compact).decode("ascii")
    return (
        _PAGE_TEMPLATE.replace("__AUDIT_DATA_BASE64__", encoded)
        .replace("__AUDIT_SHA256__", review_aid_sha256)
        .replace("__TOOL_DEFINITIONS_SHA256__", definitions_hash)
        .replace(
            "__PINNED_TOOL_DEFINITIONS_SHA256__",
            PINNED_RETAIL_TOOL_DEFINITIONS_SHA256,
        )
        .replace("__CASE_COUNT__", str(len(review_aid["cases"])))
    )


_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>Jev normalization review</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17202a;
      --muted: #667085;
      --paper: #f4f1eb;
      --surface: #ffffff;
      --surface-soft: #f8f7f4;
      --navy: #16263d;
      --navy-soft: #203957;
      --teal: #147d72;
      --teal-soft: #e6f4f1;
      --green: #237a4b;
      --green-soft: #e8f6ee;
      --red: #b23a48;
      --red-soft: #fbecef;
      --amber: #9a6700;
      --amber-soft: #fff4d6;
      --border: #d9d6cf;
      --shadow: 0 12px 30px rgba(23, 32, 42, 0.08);
      --radius: 14px;
    }

    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }
    button, textarea { font: inherit; }
    button { cursor: pointer; }
    fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
    legend { padding: 0; }
    button:focus-visible, textarea:focus-visible, input:focus-visible {
      outline: 3px solid rgba(20, 125, 114, 0.3);
      outline-offset: 2px;
    }
    .sr-only {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }

    .topbar {
      position: sticky;
      top: 0;
      z-index: 20;
      display: grid;
      grid-template-columns: minmax(260px, 1fr) minmax(320px, 520px);
      gap: 28px;
      align-items: center;
      padding: 18px 28px;
      background: var(--navy);
      color: #fff;
      box-shadow: 0 4px 18px rgba(10, 22, 38, 0.18);
    }
    .brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 42px;
      height: 42px;
      border-radius: 12px;
      background: #f6c96d;
      color: var(--navy);
      font-weight: 900;
      letter-spacing: -0.04em;
      flex: 0 0 auto;
    }
    .brand h1 { margin: 0; font-size: 18px; letter-spacing: -0.01em; }
    .brand p { margin: 2px 0 0; color: #c8d4e2; font-size: 13px; }
    .progress-block { display: grid; gap: 7px; }
    .progress-copy { display: flex; justify-content: space-between; gap: 16px; font-size: 13px; }
    .progress-copy strong { font-size: 14px; }
    .progress-track {
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(255,255,255,0.16);
    }
    .progress-fill {
      width: 0;
      height: 100%;
      background: #65d5c6;
      border-radius: inherit;
      transition: width 180ms ease;
    }

    .notice {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 9px;
      padding: 9px 20px;
      background: #fff8e5;
      color: #684b08;
      border-bottom: 1px solid #ead6a3;
      font-size: 13px;
    }
    .notice strong { color: #493300; }

    .shell {
      display: grid;
      grid-template-columns: 264px minmax(0, 1fr);
      max-width: 1500px;
      margin: 0 auto;
      min-height: calc(100vh - 110px);
    }
    .sidebar {
      position: sticky;
      top: 108px;
      align-self: start;
      height: calc(100vh - 108px);
      overflow: auto;
      padding: 22px 16px 30px;
      border-right: 1px solid var(--border);
      background: rgba(255,255,255,0.46);
    }
    .sidebar-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin: 0 4px 12px;
    }
    .sidebar-head h2 {
      margin: 0;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
    }
    .case-list { display: grid; gap: 7px; }
    .case-link {
      width: 100%;
      display: grid;
      grid-template-columns: 30px 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 10px;
      text-align: left;
      color: var(--ink);
      background: transparent;
      border: 1px solid transparent;
      border-radius: 11px;
      min-height: 52px;
    }
    .case-link:hover { background: rgba(255,255,255,0.72); }
    .case-link.active {
      background: var(--surface);
      border-color: #b9cec9;
      box-shadow: 0 4px 14px rgba(23,32,42,0.07);
    }
    .case-number {
      display: grid;
      place-items: center;
      width: 30px;
      height: 30px;
      border-radius: 9px;
      background: #e9e6df;
      font-weight: 800;
      font-size: 13px;
    }
    .case-link.active .case-number { background: var(--teal); color: #fff; }
    .case-name { min-width: 0; }
    .case-name strong { display: block; font-size: 13px; }
    .case-name span {
      display: block;
      overflow: hidden;
      color: var(--muted);
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status-dot {
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: #c9c5bd;
      box-shadow: 0 0 0 3px rgba(0,0,0,0.03);
    }
    .status-dot.progress { background: #e1a31e; }
    .status-dot.complete { background: var(--green); }
    .status-dot.issue { background: var(--red); }

    .side-actions { display: grid; gap: 8px; margin-top: 18px; }
    .content { min-width: 0; padding: 28px clamp(18px, 4vw, 54px) 60px; }
    .case-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 18px;
    }
    .eyebrow {
      margin: 0 0 5px;
      color: var(--teal);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.11em;
      text-transform: uppercase;
    }
    .case-header h2 { margin: 0; font-size: clamp(25px, 3vw, 38px); letter-spacing: -0.035em; }
    .meta { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 5px 9px;
      border-radius: 999px;
      background: #e8e5de;
      color: #4b5563;
      font-size: 12px;
      font-weight: 650;
    }
    .case-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }

    .button {
      min-height: 44px;
      padding: 8px 12px;
      border: 1px solid #c9c5bd;
      border-radius: 10px;
      background: var(--surface);
      color: var(--ink);
      font-weight: 700;
      font-size: 13px;
    }
    .button:hover { border-color: #93aaa4; background: #fbfffe; }
    .button.primary { border-color: var(--teal); background: var(--teal); color: #fff; }
    .button.primary:hover { background: #0f695f; }
    .button.ghost { background: transparent; }
    .button.danger { color: var(--red); }
    .button:disabled { opacity: 0.45; cursor: not-allowed; }

    .callout {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 20px;
      padding: 14px 16px;
      border: 1px solid #bedbd5;
      border-radius: var(--radius);
      background: var(--teal-soft);
      color: #225a54;
    }
    .callout strong { display: block; color: #154b45; }
    .callout p { margin: 3px 0 0; font-size: 13px; }

    .section-title {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 18px;
      margin: 28px 0 12px;
    }
    .section-title h3 { margin: 0; font-size: 18px; }
    .section-title p { margin: 0; color: var(--muted); font-size: 12px; }

    .check-list { display: grid; gap: 10px; }
    .check-card {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(190px, 0.8fr) auto;
      gap: 16px;
      align-items: center;
      padding: 15px 16px;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: 0 3px 12px rgba(23,32,42,0.035);
    }
    .check-copy strong { display: block; font-size: 14px; }
    .check-copy span { display: block; margin-top: 2px; color: var(--muted); font-size: 12px; }
    .auto-aid {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border-radius: 9px;
      background: var(--surface-soft);
      color: var(--muted);
      font-size: 12px;
    }
    .auto-aid.good { background: var(--green-soft); color: #296643; }
    .auto-aid.warn { background: var(--amber-soft); color: #795200; }
    .decision {
      display: inline-flex;
      overflow: hidden;
      border: 1px solid #cbc7bf;
      border-radius: 10px;
      background: #fff;
    }
    .decision label { position: relative; }
    .decision input { position: absolute; opacity: 0; pointer-events: none; }
    .decision span {
      display: block;
      min-width: 86px;
      padding: 11px 10px;
      text-align: center;
      color: #5a6470;
      font-size: 12px;
      font-weight: 800;
      border-left: 1px solid #ddd9d1;
    }
    .decision label:first-child span { border-left: 0; }
    .decision input[value="unreviewed"]:checked + span { background: #e9e6df; color: #4b5563; }
    .decision input[value="correct"]:checked + span { background: var(--green); color: #fff; }
    .decision input[value="wrong"]:checked + span { background: var(--red); color: #fff; }
    .decision input:focus-visible + span {
      position: relative;
      z-index: 1;
      box-shadow: inset 0 0 0 3px #8ed8cf;
    }

    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
      padding: 5px;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: #e9e6df;
    }
    .tab {
      flex: 0 1 auto;
      padding: 8px 12px;
      border: 0;
      border-radius: 8px;
      background: transparent;
      color: #5a6470;
      font-weight: 750;
      font-size: 13px;
    }
    .tab.active { background: #fff; color: var(--navy); box-shadow: 0 2px 8px rgba(0,0,0,0.07); }
    .evidence-panel {
      min-height: 300px;
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .turn-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      border-top: 1px solid var(--border);
    }
    .turn-row:first-child { border-top: 0; }
    .message { min-width: 0; padding: 16px; }
    .message + .message { border-left: 1px solid var(--border); }
    .message-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 9px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .role { color: var(--navy); }
    .message-content {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 13px;
    }
    .tool-box {
      margin-top: 10px;
      padding: 10px;
      border-left: 3px solid #8aa4bc;
      border-radius: 7px;
      background: #f3f6f9;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
      overflow-wrap: anywhere;
    }
    .match-strip {
      grid-column: 1 / -1;
      padding: 5px 16px;
      background: var(--green-soft);
      color: #296643;
      font-size: 11px;
      font-weight: 750;
      border-top: 1px solid #cae8d6;
    }
    .match-strip.warn { background: var(--amber-soft); color: #795200; border-top-color: #efdaa5; }
    .column-heads {
      display: grid;
      grid-template-columns: 1fr 1fr;
      background: var(--navy-soft);
      color: #fff;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .column-heads div { padding: 10px 16px; }
    .column-heads div + div { border-left: 1px solid rgba(255,255,255,0.2); }
    .panel-pad { padding: 18px; }
    .panel-pad h4 { margin: 0 0 10px; }
    .future-box {
      margin-top: 18px;
      padding: 14px;
      border: 1px dashed #deb25a;
      border-radius: 11px;
      background: #fff8e6;
    }
    .future-box h4 { color: #745000; }
    .policy-grid, .raw-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .text-panel { min-width: 0; }
    .text-panel h4 { margin: 0 0 8px; }
    pre {
      margin: 0;
      max-height: 560px;
      overflow: auto;
      padding: 14px;
      border-radius: 10px;
      background: #182333;
      color: #e8edf3;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 11px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    details {
      margin-top: 10px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--surface-soft);
    }
    summary { padding: 10px 12px; cursor: pointer; font-size: 13px; font-weight: 750; }
    details pre { border-radius: 0 0 9px 9px; }

    .notes-card {
      padding: 16px;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface);
    }
    .notes-card label { display: block; margin-bottom: 7px; font-weight: 800; font-size: 13px; }
    textarea {
      width: 100%;
      min-height: 90px;
      resize: vertical;
      padding: 11px 12px;
      border: 1px solid #c8c4bc;
      border-radius: 10px;
      background: #fff;
      color: var(--ink);
    }
    .notes-help { margin: 6px 0 0; color: var(--muted); font-size: 11px; }
    .notes-help.required { color: var(--red); font-weight: 750; }

    .bottom-nav {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 24px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
    }
    .finish-card {
      display: none;
      margin-top: 24px;
      padding: 18px;
      border: 1px solid #aed8c0;
      border-radius: var(--radius);
      background: var(--green-soft);
    }
    .finish-card.show { display: block; }
    .finish-card.issue { border-color: #efbdc5; background: var(--red-soft); }
    .finish-card h3 { margin: 0 0 5px; }
    .finish-card p { margin: 0 0 12px; font-size: 13px; }

    .toast {
      position: fixed;
      right: 22px;
      bottom: 22px;
      z-index: 50;
      max-width: 360px;
      padding: 11px 14px;
      border-radius: 10px;
      background: var(--navy);
      color: #fff;
      box-shadow: var(--shadow);
      font-size: 13px;
      opacity: 0;
      transform: translateY(12px);
      pointer-events: none;
      transition: 160ms ease;
    }
    .toast.show { opacity: 1; transform: translateY(0); }
    .muted { color: var(--muted); }
    .empty { padding: 50px 20px; text-align: center; color: var(--muted); }

    @media (max-width: 980px) {
      .topbar { grid-template-columns: 1fr; gap: 12px; padding: 14px 18px; }
      .shell { grid-template-columns: 1fr; }
      .sidebar {
        position: static;
        width: 100%;
        height: auto;
        padding: 12px 16px;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      .case-list {
        display: flex;
        overflow-x: auto;
        padding-bottom: 4px;
      }
      .case-link { min-width: 160px; }
      .side-actions { grid-template-columns: 1fr 1fr; }
      .check-card { grid-template-columns: 1fr; }
      .decision { justify-self: start; }
      .policy-grid, .raw-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 680px) {
      .notice { align-items: flex-start; justify-content: flex-start; }
      .content { padding: 20px 12px 45px; }
      .case-header { display: block; }
      .case-actions { justify-content: flex-start; margin-top: 14px; }
      .turn-row, .column-heads { grid-template-columns: 1fr; }
      .column-heads div + div, .message + .message { border-left: 0; border-top: 1px solid var(--border); }
      .decision { width: 100%; }
      .decision label { flex: 1; }
      .decision span { min-width: 0; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">J</div>
      <div>
        <h1>Source-normalization review</h1>
        <p>Data integrity check · Ayush · __CASE_COUNT__ private cases</p>
      </div>
    </div>
    <div class="progress-block" aria-live="polite">
      <div class="progress-copy">
        <span><strong id="caseProgress">0 of __CASE_COUNT__ cases complete</strong></span>
        <span id="checkProgress">0 of 60 checks decided</span>
      </div>
      <div class="progress-track" aria-hidden="true"><div class="progress-fill" id="progressFill"></div></div>
    </div>
  </header>

  <div class="notice">
    <span aria-hidden="true">🔒</span>
    <span><strong>Private and local.</strong> This page contains benchmark content. Downloaded progress/results are private working files—not the public M1 decision—and must not be committed.</span>
  </div>

  <div class="shell">
    <aside class="sidebar" aria-label="Audit cases">
      <div class="sidebar-head">
        <h2>Cases</h2>
        <span class="pill" id="issueCount">0 issues</span>
      </div>
      <nav class="case-list" id="caseList"></nav>
      <div class="side-actions">
        <button class="button" id="nextIncomplete">Next incomplete</button>
        <button class="button ghost" id="downloadProgress">Download progress</button>
        <button class="button ghost" id="importProgress">Import progress</button>
        <input id="progressFile" type="file" accept="application/json,.json" hidden>
        <button class="button ghost danger" id="resetAll">Clear all</button>
      </div>
    </aside>

    <main class="content" id="mainContent">
      <div class="case-header">
        <div>
          <p class="eyebrow" id="caseEyebrow">Case 01</p>
          <h2 id="caseTitle">Loading review…</h2>
          <div class="meta" id="caseMeta"></div>
        </div>
        <div class="case-actions">
          <button class="button" id="markAllCorrect">Mark all six correct</button>
          <button class="button ghost danger" id="clearCase">Clear this case</button>
        </div>
      </div>

      <div class="callout">
        <span aria-hidden="true">✓</span>
        <div>
          <strong>You are checking the data conversion—not grading the agent.</strong>
          <p>Inspect the aligned evidence, then choose Correct or Wrong for each check. Automated comparisons are hints, not your decision.</p>
        </div>
      </div>

      <div class="section-title">
        <h3>Your six decisions</h3>
        <p>All decisions autosave locally.</p>
      </div>
      <section class="check-list" id="checkList" aria-label="Review checks"></section>

      <div class="section-title">
        <h3>Evidence</h3>
        <p>Tool-call IDs are intentionally rewritten; compare names, arguments, and pairing.</p>
      </div>
      <div class="tabs" role="tablist" aria-label="Evidence views">
        <button class="tab active" role="tab" aria-selected="true" data-tab="conversation">Conversation</button>
        <button class="tab" role="tab" aria-selected="false" tabindex="-1" data-tab="target">Target & future</button>
        <button class="tab" role="tab" aria-selected="false" tabindex="-1" data-tab="policy">Policy & tools</button>
        <button class="tab" role="tab" aria-selected="false" tabindex="-1" data-tab="raw">Raw JSON</button>
      </div>
      <section class="evidence-panel" id="evidencePanel" aria-live="polite"></section>

      <div class="section-title"><h3>Notes</h3></div>
      <section class="notes-card">
        <label for="caseNotes">What is wrong or uncertain?</label>
        <textarea id="caseNotes" placeholder="Only required when you mark something Wrong."></textarea>
        <p class="notes-help" id="notesHelp">Optional when all six checks are correct.</p>
      </section>

      <section class="finish-card" id="finishCard">
        <h3 id="finishTitle">Review complete</h3>
        <p id="finishCopy"></p>
        <button class="button primary" id="finishReview">Download private final review</button>
      </section>

      <div class="bottom-nav">
        <button class="button" id="previousCase">← Previous case</button>
        <span class="muted" id="positionLabel">Case 1 of __CASE_COUNT__</span>
        <button class="button primary" id="nextCase">Next case →</button>
      </div>
    </main>
  </div>

  <div class="toast" id="toast" role="status" aria-live="polite"></div>

  <script>
    "use strict";

    var AUDIT_SHA256 = "__AUDIT_SHA256__";
    var TOOL_DEFINITIONS_SHA256 = "__TOOL_DEFINITIONS_SHA256__";
    var PINNED_TOOL_DEFINITIONS_SHA256 = "__PINNED_TOOL_DEFINITIONS_SHA256__";
    var AUDIT_DATA_BASE64 = "__AUDIT_DATA_BASE64__";
    var requiredChecks = [
      "conversation_order_matches_source",
      "target_response_matches_source",
      "terminal_future_message_excluded",
      "tool_calls_and_results_preserved_and_paired",
      "policy_and_tool_definitions_match_pinned_source",
      "forbidden_fields_absent"
    ];
    var checkDefinitions = {
      conversation_order_matches_source: {
        label: "Conversation order matches",
        help: "Every pre-target turn appears once, in the same order, with the same role and content."
      },
      target_response_matches_source: {
        label: "Target response matches",
        help: "The target assistant response is copied exactly from the source turn."
      },
      terminal_future_message_excluded: {
        label: "Future message is excluded",
        help: "Any message after the target appears only on the source side, never in the packet."
      },
      tool_calls_and_results_preserved_and_paired: {
        label: "Tool calls and results are preserved",
        help: "Tool names, arguments, results, errors, and call/result pairing remain intact."
      },
      policy_and_tool_definitions_match_pinned_source: {
        label: "Policy and tool definitions match",
        help: "The embedded policy is identical and the packet carries the pinned retail tool definitions."
      },
      forbidden_fields_absent: {
        label: "Forbidden fields are absent",
        help: "No rewards, model identity, costs, expected answers, or future messages leaked into the packet."
      }
    };
    var forbiddenKeys = new Set([
      "reward", "reward_info", "human_label", "human_labels", "labels",
      "reference_outputs", "expected_actions", "evaluation_criteria",
      "evaluator_output", "judge_output", "raw_data", "usage", "cost",
      "agent_model", "source_model", "user_scenario", "future_messages"
    ]);
    var audit = decodeAudit();
    var storageKey = "jev-normalization-review:" + AUDIT_SHA256;
    var state = loadState();
    var currentIndex = Math.max(0, Math.min(audit.cases.length - 1, state.currentIndex || 0));
    var activeTab = "conversation";
    var toastTimer = null;

    function decodeAudit() {
      var binary = atob(AUDIT_DATA_BASE64);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      return JSON.parse(new TextDecoder("utf-8").decode(bytes));
    }

    function freshState() {
      return {version: 1, auditSha256: AUDIT_SHA256, currentIndex: 0, cases: {}};
    }

    function loadState() {
      try {
        var parsed = JSON.parse(localStorage.getItem(storageKey) || "null");
        if (parsed && parsed.version === 1 && parsed.auditSha256 === AUDIT_SHA256 && parsed.cases) return parsed;
      } catch (error) {
        showToast("Browser storage is unavailable. Use Download progress to save your work.");
      }
      return freshState();
    }

    function saveState() {
      state.currentIndex = currentIndex;
      try {
        localStorage.setItem(storageKey, JSON.stringify(state));
      } catch (error) {
        showToast("Could not autosave. Download progress before closing the page.");
      }
    }

    function caseState(caseData) {
      if (!state.cases[caseData.example_id]) {
        state.cases[caseData.example_id] = {checks: {}, notes: ""};
      }
      return state.cases[caseData.example_id];
    }

    function decided(value) { return value === true || value === false; }

    function caseSummary(caseData) {
      var saved = caseState(caseData);
      var values = requiredChecks.map(function(key) { return saved.checks[key]; });
      var decidedCount = values.filter(decided).length;
      var wrongCount = values.filter(function(value) { return value === false; }).length;
      var needsNote = wrongCount > 0 && !String(saved.notes || "").trim();
      return {
        decided: decidedCount,
        wrong: wrongCount,
        complete: decidedCount === requiredChecks.length && !needsNote,
        needsNote: needsNote
      };
    }

    function totals() {
      var checksDecided = 0;
      var casesComplete = 0;
      var wrong = 0;
      audit.cases.forEach(function(caseData) {
        var summary = caseSummary(caseData);
        checksDecided += summary.decided;
        wrong += summary.wrong;
        if (summary.complete) casesComplete += 1;
      });
      return {checksDecided: checksDecided, casesComplete: casesComplete, wrong: wrong};
    }

    function render() {
      renderProgress();
      renderCaseList();
      renderCurrentCase();
      saveState();
    }

    function renderProgress() {
      var summary = totals();
      var totalChecks = audit.cases.length * requiredChecks.length;
      document.getElementById("caseProgress").textContent =
        summary.casesComplete + " of " + audit.cases.length + " cases complete";
      document.getElementById("checkProgress").textContent =
        summary.checksDecided + " of " + totalChecks + " checks decided";
      document.getElementById("progressFill").style.width =
        String((summary.checksDecided / totalChecks) * 100) + "%";
      document.getElementById("issueCount").textContent =
        summary.wrong + (summary.wrong === 1 ? " issue" : " issues");
    }

    function renderCaseList() {
      var html = audit.cases.map(function(caseData, index) {
        var summary = caseSummary(caseData);
        var statusClass = summary.wrong ? "issue" : summary.complete ? "complete" : summary.decided ? "progress" : "";
        var statusText = summary.wrong ? "has wrong" : summary.complete ? "complete" :
          summary.decided ? "in progress" : "unreviewed";
        return '<button class="case-link ' + (index === currentIndex ? "active" : "") +
          '" data-case-index="' + index + '" aria-label="Open case ' + caseData.case_number +
          ', ' + statusText + '"' + (index === currentIndex ? ' aria-current="step"' : "") + '>' +
          '<span class="case-number">' + String(caseData.case_number).padStart(2, "0") + '</span>' +
          '<span class="case-name"><strong>Case ' + String(caseData.case_number).padStart(2, "0") +
          '</strong><span>' + escapeHtml(caseData.task_family_id) + '</span></span>' +
          '<span class="status-dot ' + statusClass + '" title="' + statusText + '" aria-hidden="true"></span></button>';
      }).join("");
      document.getElementById("caseList").innerHTML = html;
    }

    function renderCurrentCase() {
      var caseData = audit.cases[currentIndex];
      var saved = caseState(caseData);
      var summary = caseSummary(caseData);
      document.getElementById("caseEyebrow").textContent =
        "Case " + String(caseData.case_number).padStart(2, "0");
      document.getElementById("caseTitle").textContent = "Verify the normalized evidence";
      renderCaseMeta(caseData, summary);
      document.getElementById("positionLabel").textContent =
        "Case " + (currentIndex + 1) + " of " + audit.cases.length;
      document.getElementById("previousCase").disabled = currentIndex === 0;
      document.getElementById("nextCase").textContent =
        currentIndex === audit.cases.length - 1 ? "Review summary →" : "Next case →";
      document.getElementById("checkList").innerHTML = renderChecks(caseData, saved);
      document.getElementById("caseNotes").value = saved.notes || "";
      renderNotesHelp(summary);
      renderEvidence(caseData);
      renderFinishCard();
    }

    function renderCaseMeta(caseData, summary) {
      document.getElementById("caseMeta").innerHTML =
        pill(caseData.size_band + " packet") +
        pill(formatNumber(caseData.packet_size_bytes) + " bytes") +
        pill(caseData.task_family_id) +
        pill(summary.decided + "/6 decided");
    }

    function pill(text) { return '<span class="pill">' + escapeHtml(String(text)) + '</span>'; }

    function renderChecks(caseData, saved) {
      var automatic = automaticChecks(caseData);
      return requiredChecks.map(function(key) {
        var definition = checkDefinitions[key];
        var aid = automatic[key];
        var value = saved.checks[key];
        return '<fieldset class="check-card"><legend class="sr-only">' +
          escapeHtml(definition.label) + '</legend>' +
          '<div class="check-copy"><strong>' + escapeHtml(definition.label) + '</strong>' +
          '<span>' + escapeHtml(definition.help) + '</span></div>' +
          '<div class="auto-aid ' + (aid.pass ? "good" : "warn") + '">' +
          '<span aria-hidden="true">' + (aid.pass ? "●" : "▲") + '</span>' +
          '<span><strong>Automated aid:</strong> ' + escapeHtml(aid.summary) + '</span></div>' +
          '<div class="decision">' +
          decisionOption(key, "unreviewed", "— Clear", !decided(value)) +
          decisionOption(key, "correct", "✓ Correct", value === true) +
          decisionOption(key, "wrong", "✕ Wrong", value === false) +
          '</div></fieldset>';
      }).join("");
    }

    function decisionOption(key, value, label, checked) {
      return '<label><input type="radio" name="decision-' + escapeHtml(key) +
        '" data-check-key="' + escapeHtml(key) + '" value="' + value + '"' +
        (checked ? " checked" : "") + '><span>' + label + '</span></label>';
    }

    function automaticChecks(caseData) {
      var source = caseData.original_source_simulation || {};
      var packet = caseData.canonical_packet || {};
      var messages = Array.isArray(source.messages) ? source.messages : [];
      var prefix = Array.isArray(packet.conversation_prefix) ? packet.conversation_prefix : [];
      var targetTurn = packet.target_response ? packet.target_response.turn_idx : -1;
      var sourcePrefix = messages.filter(function(message) { return message.turn_idx < targetTurn; });
      var sourceTarget = messages.find(function(message) { return message.turn_idx === targetTurn; });
      var future = messages.filter(function(message) { return message.turn_idx > targetTurn; });
      var conversationMatches = sourcePrefix.length === prefix.length &&
        sourcePrefix.every(function(message, index) {
          return semanticMessageEqual(message, prefix[index]);
        });
      var targetMatches = !!sourceTarget && !!packet.target_response &&
        sourceTarget.role === packet.target_response.role &&
        sourceTarget.turn_idx === packet.target_response.turn_idx &&
        normalizeNullable(sourceTarget.content) === normalizeNullable(packet.target_response.content) &&
        (!Array.isArray(sourceTarget.tool_calls) || sourceTarget.tool_calls.length === 0);
      var canonicalTurns = prefix.map(function(message) { return message.turn_idx; });
      var futureText = future.length === 1 ? String(future[0].content || "") : "";
      var futureExcluded = future.length === 1 &&
        /###(?:STOP|TRANSFER)###/.test(futureText) &&
        JSON.stringify(packet).indexOf(futureText) === -1 &&
        canonicalTurns.every(function(turn) { return turn < targetTurn; }) &&
        packet.target_response && packet.target_response.turn_idx === targetTurn;
      var toolPairs = toolPairingValid(sourcePrefix, false) &&
        toolPairingValid(prefix, true) &&
        semanticToolsMatch(sourcePrefix, prefix) &&
        toolCrosswalkValid(sourcePrefix, prefix);
      var policyMatches = source.policy === packet.policy &&
        Array.isArray(packet.tool_definitions) &&
        packet.tool_definitions.length > 0 &&
        TOOL_DEFINITIONS_SHA256 === PINNED_TOOL_DEFINITIONS_SHA256;
      var forbidden = findForbiddenEvidence(packet, source);
      return {
        conversation_order_matches_source: {
          pass: conversationMatches,
          summary: conversationMatches ? sourcePrefix.length + " aligned turns match" : "turn mismatch found"
        },
        target_response_matches_source: {
          pass: targetMatches,
          summary: targetMatches ? "target role and content match" : "target mismatch found"
        },
        terminal_future_message_excluded: {
          pass: futureExcluded,
          summary: futureExcluded ? "one terminal message fully excluded" :
            "future-message boundary needs attention"
        },
        tool_calls_and_results_preserved_and_paired: {
          pass: toolPairs,
          summary: toolPairs ? "tool semantics and pairing match" : "tool mismatch found"
        },
        policy_and_tool_definitions_match_pinned_source: {
          pass: policyMatches,
          summary: policyMatches ? "policy exact; " + packet.tool_definitions.length + " pinned tools match" : "policy/tools need attention"
        },
        forbidden_fields_absent: {
          pass: forbidden.length === 0,
          summary: forbidden.length === 0 ? "no forbidden keys found" : "found: " + forbidden.join(", ")
        }
      };
    }

    function normalizeNullable(value) { return value === null || value === undefined ? null : value; }

    function semanticMessageEqual(source, packet) {
      if (!source || !packet) return false;
      if (source.role !== packet.role || source.turn_idx !== packet.turn_idx) return false;
      if (normalizeNullable(source.content) !== normalizeNullable(packet.content)) return false;
      var sourceCalls = Array.isArray(source.tool_calls) ? source.tool_calls : [];
      var packetCalls = Array.isArray(packet.tool_calls) ? packet.tool_calls : [];
      if (sourceCalls.length !== packetCalls.length) return false;
      for (var i = 0; i < sourceCalls.length; i += 1) {
        if (sourceCalls[i].name !== packetCalls[i].name) return false;
        if (stableJson(sourceCalls[i].arguments) !== stableJson(packetCalls[i].arguments)) return false;
        if (normalizeNullable(sourceCalls[i].requestor) !== normalizeNullable(packetCalls[i].requestor)) return false;
      }
      if (source.role === "tool") {
        if (Boolean(source.error) !== Boolean(packet.error)) return false;
        if (normalizeNullable(source.requestor) !== normalizeNullable(packet.requestor)) return false;
      }
      return true;
    }

    function semanticToolsMatch(sourcePrefix, packetPrefix) {
      var sourceTools = sourcePrefix.filter(function(message) {
        return message.role === "tool" || (Array.isArray(message.tool_calls) && message.tool_calls.length);
      });
      var packetTools = packetPrefix.filter(function(message) {
        return message.role === "tool" || (Array.isArray(message.tool_calls) && message.tool_calls.length);
      });
      return sourceTools.length === packetTools.length &&
        sourceTools.every(function(message, index) { return semanticMessageEqual(message, packetTools[index]); });
    }

    function toolPairingValid(prefix, requireCanonicalIds) {
      var calls = [];
      var results = [];
      var seenCalls = new Set();
      var seenResults = new Set();
      var ordered = true;
      prefix.forEach(function(message) {
        (message.tool_calls || []).forEach(function(call) {
          calls.push(call.id);
          seenCalls.add(call.id);
        });
        if (message.role === "tool") {
          results.push(message.id);
          if (!seenCalls.has(message.id) || seenResults.has(message.id)) ordered = false;
          seenResults.add(message.id);
        }
      });
      if (!ordered) return false;
      if (calls.length !== new Set(calls).size || results.length !== new Set(results).size) return false;
      if (calls.length !== results.length) return false;
      if (!calls.every(function(id) { return results.indexOf(id) !== -1; })) return false;
      if (requireCanonicalIds && !calls.every(function(id) {
        return /^call-[0-9a-f]{24}$/.test(String(id));
      })) return false;
      return true;
    }

    function toolCrosswalkValid(sourcePrefix, packetPrefix) {
      if (sourcePrefix.length !== packetPrefix.length) return false;
      var sourceToPacketId = new Map();
      for (var index = 0; index < sourcePrefix.length; index += 1) {
        var sourceMessage = sourcePrefix[index];
        var packetMessage = packetPrefix[index];
        var sourceCalls = Array.isArray(sourceMessage.tool_calls) ? sourceMessage.tool_calls : [];
        var packetCalls = Array.isArray(packetMessage.tool_calls) ? packetMessage.tool_calls : [];
        if (sourceCalls.length !== packetCalls.length) return false;
        for (var callIndex = 0; callIndex < sourceCalls.length; callIndex += 1) {
          var sourceId = sourceCalls[callIndex].id;
          var packetId = packetCalls[callIndex].id;
          if (sourceToPacketId.has(sourceId) && sourceToPacketId.get(sourceId) !== packetId) return false;
          sourceToPacketId.set(sourceId, packetId);
        }
        if (sourceMessage.role === "tool") {
          if (packetMessage.role !== "tool") return false;
          if (sourceToPacketId.get(sourceMessage.id) !== packetMessage.id) return false;
        }
      }
      return true;
    }

    function findForbiddenKeys(value, path, found) {
      path = path || "";
      found = found || [];
      if (Array.isArray(value)) {
        value.forEach(function(item, index) { findForbiddenKeys(item, path + "[" + index + "]", found); });
      } else if (value && typeof value === "object") {
        Object.keys(value).forEach(function(key) {
          if (forbiddenKeys.has(key)) found.push(path ? path + "." + key : key);
          findForbiddenKeys(value[key], path ? path + "." + key : key, found);
        });
      }
      return found;
    }

    function findForbiddenEvidence(packet, source) {
      var found = findForbiddenKeys(packet);
      var allowedTopLevel = new Set([
        "schema_version", "example_id", "policy", "tool_definitions",
        "conversation_prefix", "target_response"
      ]);
      Object.keys(packet).forEach(function(key) {
        if (!allowedTopLevel.has(key)) found.push("unexpected top-level key: " + key);
      });
      var serialized = JSON.stringify(packet);
      if (/toolu_[A-Za-z0-9]+/.test(serialized)) found.push("raw toolu_ ID");
      if (/###(?:STOP|TRANSFER)###/.test(serialized)) found.push("terminal control token");
      [source.id, source.provider_session_id].forEach(function(identifier) {
        if (identifier && serialized.indexOf(String(identifier)) !== -1) {
          found.push("source/provider identifier");
        }
      });
      return Array.from(new Set(found));
    }

    function stableJson(value) {
      if (Array.isArray(value)) return "[" + value.map(stableJson).join(",") + "]";
      if (value && typeof value === "object") {
        return "{" + Object.keys(value).sort().map(function(key) {
          return JSON.stringify(key) + ":" + stableJson(value[key]);
        }).join(",") + "}";
      }
      return JSON.stringify(value);
    }

    function renderEvidence(caseData) {
      var panel = document.getElementById("evidencePanel");
      if (activeTab === "conversation") panel.innerHTML = conversationView(caseData);
      else if (activeTab === "target") panel.innerHTML = targetView(caseData);
      else if (activeTab === "policy") panel.innerHTML = policyView(caseData);
      else panel.innerHTML = rawView(caseData);
    }

    function conversationView(caseData) {
      var source = caseData.original_source_simulation;
      var packet = caseData.canonical_packet;
      var targetTurn = packet.target_response.turn_idx;
      var sourceByTurn = new Map((source.messages || []).map(function(message) {
        return [message.turn_idx, message];
      }));
      var rows = packet.conversation_prefix.map(function(message) {
        var original = sourceByTurn.get(message.turn_idx);
        var matches = semanticMessageEqual(original, message);
        return '<div class="turn-row">' +
          messageCard(original, "Source turn " + message.turn_idx) +
          messageCard(message, "Normalized turn " + message.turn_idx) +
          '<div class="match-strip ' + (matches ? "" : "warn") + '">' +
          (matches ? "✓ Role, content, and tool semantics match" : "▲ Difference detected—inspect this turn") +
          '</div></div>';
      }).join("");
      return '<div class="column-heads"><div>Original source</div><div>Canonical packet</div></div>' +
        (rows || '<div class="empty">No pre-target turns.</div>') +
        '<div class="panel-pad muted">Target begins at turn ' + targetTurn +
        '. Open “Target & future” to inspect it and any excluded terminal messages.</div>';
    }

    function targetView(caseData) {
      var source = caseData.original_source_simulation;
      var packet = caseData.canonical_packet;
      var targetTurn = packet.target_response.turn_idx;
      var sourceTarget = (source.messages || []).find(function(message) { return message.turn_idx === targetTurn; });
      var future = (source.messages || []).filter(function(message) { return message.turn_idx > targetTurn; });
      var matches = sourceTarget && sourceTarget.content === packet.target_response.content &&
        sourceTarget.role === packet.target_response.role;
      var futureHtml = future.length ? future.map(function(message) {
        return messageCard(message, "Excluded source turn " + message.turn_idx);
      }).join("") : '<p class="muted">The source has no message after the target.</p>';
      return '<div class="column-heads"><div>Source target</div><div>Packet target</div></div>' +
        '<div class="turn-row">' + messageCard(sourceTarget, "Source turn " + targetTurn) +
        messageCard(packet.target_response, "Target response") +
        '<div class="match-strip ' + (matches ? "" : "warn") + '">' +
        (matches ? "✓ Target role and content match exactly" : "▲ Target difference detected") +
        '</div></div><div class="panel-pad"><div class="future-box"><h4>Messages after the target</h4>' +
        '<p class="muted">These must remain source-only and must not appear in the packet.</p>' +
        futureHtml + '</div></div>';
    }

    function policyView(caseData) {
      var source = caseData.original_source_simulation;
      var packet = caseData.canonical_packet;
      var matches = source.policy === packet.policy &&
        TOOL_DEFINITIONS_SHA256 === PINNED_TOOL_DEFINITIONS_SHA256;
      var tools = packet.tool_definitions || [];
      var toolDetails = tools.map(function(tool) {
        return '<details><summary>' + escapeHtml(tool.name) + '</summary><pre>' +
          escapeHtml(JSON.stringify(tool, null, 2)) + '</pre></details>';
      }).join("");
      return '<div class="panel-pad"><div class="auto-aid ' + (matches ? "good" : "warn") + '">' +
        (matches ? "● Policy text is identical and the tool-definition hash matches the pinned source." :
          "▲ Policy text or pinned tool definitions differ.") +
        '</div><p class="muted">Verified pinned definitions SHA-256: <code>' +
        escapeHtml(TOOL_DEFINITIONS_SHA256) + '</code></p>' +
        '<div class="policy-grid" style="margin-top:12px">' +
        '<div class="text-panel"><h4>Source policy</h4><pre>' + escapeHtml(source.policy || "") + '</pre></div>' +
        '<div class="text-panel"><h4>Packet policy</h4><pre>' + escapeHtml(packet.policy || "") + '</pre></div>' +
        '</div><div class="section-title"><h3>Pinned tool definitions</h3><p>' +
        tools.length + ' definitions in the packet</p></div>' + toolDetails + '</div>';
    }

    function rawView(caseData) {
      return '<div class="panel-pad raw-grid">' +
        '<div class="text-panel"><h4>Original source simulation</h4><pre>' +
        escapeHtml(JSON.stringify(caseData.original_source_simulation, null, 2)) + '</pre></div>' +
        '<div class="text-panel"><h4>Canonical packet</h4><pre>' +
        escapeHtml(JSON.stringify(caseData.canonical_packet, null, 2)) + '</pre></div></div>';
    }

    function messageCard(message, label) {
      if (!message) return '<div class="message"><div class="message-head"><span>' +
        escapeHtml(label) + '</span></div><div class="message-content muted">Missing</div></div>';
      var calls = (message.tool_calls || []).map(function(call) {
        return '<div class="tool-box"><strong>Call · ' + escapeHtml(call.name || "unknown") +
          '</strong><br><span class="muted">ID: ' + escapeHtml(call.id || "none") +
          '</span><br>' + escapeHtml(JSON.stringify(call.arguments || {}, null, 2)) + '</div>';
      }).join("");
      var result = message.role === "tool" ?
        '<div class="tool-box"><strong>Tool result' + (message.error ? " · error" : "") +
        '</strong><br><span class="muted">ID: ' + escapeHtml(message.id || "none") +
        '</span><br>' + escapeHtml(String(message.content || "")) + '</div>' : "";
      var content = message.content === null || message.content === undefined || message.content === "" ?
        '<span class="muted">No text content</span>' : escapeHtml(String(message.content));
      return '<div class="message"><div class="message-head"><span>' + escapeHtml(label) +
        '</span><span class="role">' + escapeHtml(message.role || "unknown") + '</span></div>' +
        '<div class="message-content">' + content + '</div>' + calls + result + '</div>';
    }

    function renderNotesHelp(summary) {
      var help = document.getElementById("notesHelp");
      if (summary.needsNote) {
        help.textContent = "A note is required because this case contains a Wrong decision.";
        help.className = "notes-help required";
      } else {
        help.textContent = "Optional when all six checks are correct.";
        help.className = "notes-help";
      }
    }

    function renderFinishCard() {
      var summary = totals();
      var complete = summary.casesComplete === audit.cases.length;
      var card = document.getElementById("finishCard");
      card.className = "finish-card" + (complete ? " show" : "") + (summary.wrong ? " issue" : "");
      if (!complete) return;
      document.getElementById("finishTitle").textContent =
        summary.wrong ? "Review complete with issues" : "All ten cases pass";
      document.getElementById("finishCopy").textContent =
        summary.wrong ? summary.wrong + " Wrong decision(s) are documented. Download the private result for recording." :
          "All 60 checks are marked Correct. Download the private result for sign-off.";
    }

    function goTo(index) {
      currentIndex = Math.max(0, Math.min(audit.cases.length - 1, index));
      render();
      document.getElementById("mainContent").scrollIntoView({behavior: "smooth", block: "start"});
    }

    function nextIncomplete() {
      for (var offset = 1; offset <= audit.cases.length; offset += 1) {
        var index = (currentIndex + offset) % audit.cases.length;
        if (!caseSummary(audit.cases[index]).complete) {
          goTo(index);
          return;
        }
      }
      showToast("All cases are complete.");
    }

    function buildExport() {
      var summary = totals();
      var complete = summary.casesComplete === audit.cases.length;
      return {
        schema_version: "1.0",
        artifact_type: "private_m1_normalization_audit_human_review",
        privacy: "private_contains_example_ids_and_source_hashes_do_not_commit",
        reviewer: "Ayush",
        reviewed_at: complete ? new Date().toISOString() : null,
        status: complete ? (summary.wrong ? "completed_with_issues" : "all_checks_passed") : "in_progress",
        source_review_aid_sha256: AUDIT_SHA256,
        summary: {
          cases_total: audit.cases.length,
          cases_complete: summary.casesComplete,
          checks_total: audit.cases.length * requiredChecks.length,
          checks_decided: summary.checksDecided,
          wrong_decisions: summary.wrong
        },
        cases: audit.cases.map(function(caseData) {
          var saved = caseState(caseData);
          var checks = {};
          requiredChecks.forEach(function(key) {
            checks[key] = decided(saved.checks[key]) ? saved.checks[key] : null;
          });
          return {
            case_number: caseData.case_number,
            example_id: caseData.example_id,
            canonical_packet_hash: caseData.canonical_packet_hash,
            source_simulation_hash: caseData.source_simulation_hash,
            checks: checks,
            notes: String(saved.notes || "").trim() || null
          };
        })
      };
    }

    function restoreProgress(result) {
      if (!result || typeof result !== "object" || Array.isArray(result)) {
        throw new Error("the file is not a review object");
      }
      if (result.schema_version !== "1.0" ||
          result.artifact_type !== "private_m1_normalization_audit_human_review" ||
          result.privacy !== "private_contains_example_ids_and_source_hashes_do_not_commit" ||
          result.source_review_aid_sha256 !== AUDIT_SHA256 ||
          !Array.isArray(result.cases) || result.cases.length !== audit.cases.length) {
        throw new Error("the file does not belong to this private review page");
      }
      var imported = freshState();
      result.cases.forEach(function(incoming, index) {
        var expected = audit.cases[index];
        if (!incoming || typeof incoming !== "object" ||
            incoming.case_number !== expected.case_number ||
            incoming.example_id !== expected.example_id ||
            incoming.canonical_packet_hash !== expected.canonical_packet_hash ||
            incoming.source_simulation_hash !== expected.source_simulation_hash ||
            !incoming.checks || typeof incoming.checks !== "object" || Array.isArray(incoming.checks)) {
          throw new Error("case " + (index + 1) + " does not match this review");
        }
        var keys = Object.keys(incoming.checks).sort();
        var expectedKeys = requiredChecks.slice().sort();
        if (stableJson(keys) !== stableJson(expectedKeys)) {
          throw new Error("case " + (index + 1) + " has unexpected checks");
        }
        var checks = {};
        requiredChecks.forEach(function(key) {
          var value = incoming.checks[key];
          if (value !== null && value !== true && value !== false) {
            throw new Error("case " + (index + 1) + " has an invalid decision");
          }
          if (value === true || value === false) checks[key] = value;
        });
        if (incoming.notes !== null && typeof incoming.notes !== "string") {
          throw new Error("case " + (index + 1) + " has invalid notes");
        }
        imported.cases[expected.example_id] = {
          checks: checks,
          notes: incoming.notes || ""
        };
      });
      state = imported;
      currentIndex = 0;
      for (var index = 0; index < audit.cases.length; index += 1) {
        if (!caseSummary(audit.cases[index]).complete) {
          currentIndex = index;
          break;
        }
      }
      render();
    }

    function downloadReview(finalOnly) {
      var result = buildExport();
      if (finalOnly && result.status === "in_progress") {
        showToast("Complete all decisions and add notes for Wrong items first.");
        return;
      }
      var blob = new Blob([JSON.stringify(result, null, 2) + "\\n"], {type: "application/json"});
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = finalOnly ?
        "PRIVATE-M1-normalization-audit-review-result.json" :
        "PRIVATE-M1-normalization-audit-review-progress.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
      showToast(finalOnly ? "Private final review downloaded." : "Private progress downloaded.");
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function formatNumber(value) {
      return Number(value).toLocaleString("en-US");
    }

    function showToast(message) {
      var toast = document.getElementById("toast");
      if (!toast) return;
      toast.textContent = message;
      toast.classList.add("show");
      clearTimeout(toastTimer);
      toastTimer = setTimeout(function() { toast.classList.remove("show"); }, 2600);
    }

    document.getElementById("caseList").addEventListener("click", function(event) {
      var button = event.target.closest("[data-case-index]");
      if (button) goTo(Number(button.dataset.caseIndex));
    });
    document.getElementById("checkList").addEventListener("change", function(event) {
      var input = event.target.closest("[data-check-key]");
      if (!input) return;
      var saved = caseState(audit.cases[currentIndex]);
      if (input.value === "unreviewed") delete saved.checks[input.dataset.checkKey];
      else saved.checks[input.dataset.checkKey] = input.value === "correct";
      var summary = caseSummary(audit.cases[currentIndex]);
      renderProgress();
      renderCaseList();
      renderCaseMeta(audit.cases[currentIndex], summary);
      renderNotesHelp(summary);
      renderFinishCard();
      saveState();
    });
    document.getElementById("caseNotes").addEventListener("input", function(event) {
      caseState(audit.cases[currentIndex]).notes = event.target.value;
      renderProgress();
      renderCaseList();
      renderNotesHelp(caseSummary(audit.cases[currentIndex]));
      renderFinishCard();
      saveState();
    });
    document.querySelector(".tabs").addEventListener("click", function(event) {
      var button = event.target.closest("[data-tab]");
      if (!button) return;
      activeTab = button.dataset.tab;
      document.querySelectorAll(".tab").forEach(function(tab) {
        var active = tab.dataset.tab === activeTab;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.tabIndex = active ? 0 : -1;
      });
      renderEvidence(audit.cases[currentIndex]);
    });
    document.querySelector(".tabs").addEventListener("keydown", function(event) {
      var button = event.target.closest("[data-tab]");
      if (!button || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      var tabs = Array.from(document.querySelectorAll(".tab"));
      var index = tabs.indexOf(button);
      if (event.key === "Home") index = 0;
      else if (event.key === "End") index = tabs.length - 1;
      else index = (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[index].focus();
      tabs[index].click();
    });
    document.getElementById("markAllCorrect").addEventListener("click", function() {
      var saved = caseState(audit.cases[currentIndex]);
      requiredChecks.forEach(function(key) { saved.checks[key] = true; });
      render();
      showToast("All six checks marked Correct for this case.");
    });
    document.getElementById("clearCase").addEventListener("click", function() {
      if (!confirm("Clear all decisions and notes for this case?")) return;
      state.cases[audit.cases[currentIndex].example_id] = {checks: {}, notes: ""};
      render();
    });
    document.getElementById("resetAll").addEventListener("click", function() {
      if (!confirm("Clear the entire ten-case review? This cannot be undone.")) return;
      state = freshState();
      currentIndex = 0;
      render();
      showToast("Review cleared.");
    });
    document.getElementById("nextIncomplete").addEventListener("click", nextIncomplete);
    document.getElementById("downloadProgress").addEventListener("click", function() { downloadReview(false); });
    document.getElementById("importProgress").addEventListener("click", function() {
      document.getElementById("progressFile").click();
    });
    document.getElementById("progressFile").addEventListener("change", async function(event) {
      var file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        restoreProgress(JSON.parse(await file.text()));
        showToast("Private review progress restored.");
      } catch (error) {
        showToast("Import rejected: " + (error && error.message ? error.message : "invalid file"));
      } finally {
        event.target.value = "";
      }
    });
    document.getElementById("finishReview").addEventListener("click", function() { downloadReview(true); });
    document.getElementById("previousCase").addEventListener("click", function() { goTo(currentIndex - 1); });
    document.getElementById("nextCase").addEventListener("click", function() {
      if (currentIndex < audit.cases.length - 1) goTo(currentIndex + 1);
      else {
        var summary = totals();
        if (summary.casesComplete === audit.cases.length) {
          document.getElementById("finishCard").scrollIntoView({behavior: "smooth", block: "center"});
        } else {
          nextIncomplete();
        }
      }
    });
    document.addEventListener("keydown", function(event) {
      if (event.target.matches("textarea, input, button, select, a")) return;
      if (event.key === "ArrowLeft" && currentIndex > 0) goTo(currentIndex - 1);
      if (event.key === "ArrowRight" && currentIndex < audit.cases.length - 1) goTo(currentIndex + 1);
    });

    render();
  </script>
</body>
</html>
"""
