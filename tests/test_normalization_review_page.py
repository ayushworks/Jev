from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from judge_compare.normalization_review_page import (
    NormalizationReviewPageError,
    _render_review_page,
    _validate_review_aid,
)
from judge_compare.packet_builder import PINNED_RETAIL_TOOL_DEFINITIONS_SHA256

ROOT = Path(__file__).resolve().parents[1]
REVIEW_AID_PATH = (
    ROOT
    / "data"
    / "packets"
    / "v1"
    / "audits"
    / "M1-normalization-audit-review-aid.json"
)


def _actual_review_aid() -> dict:
    value = json.loads(REVIEW_AID_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _embedded_review_aid(page: str) -> dict:
    match = re.search(r'var AUDIT_DATA_BASE64 = "([^"]+)";', page)
    assert match is not None
    value = json.loads(base64.b64decode(match.group(1)))
    assert isinstance(value, dict)
    return value


def test_actual_review_aid_validates_and_round_trips_through_safe_embedding() -> None:
    review_aid = _actual_review_aid()
    _validate_review_aid(review_aid)

    page = _render_review_page(review_aid, review_aid_sha256="a" * 64)

    assert _embedded_review_aid(page) == review_aid
    assert 'var AUDIT_SHA256 = "' + ("a" * 64) + '";' in page
    assert "Correct" in page
    assert "Wrong" in page
    assert "Next incomplete" in page
    assert "Download private final review" in page
    assert "Import progress" in page
    assert "restoreProgress" in page
    assert PINNED_RETAIL_TOOL_DEFINITIONS_SHA256 in page
    assert "localStorage" in page
    assert "http://" not in page
    assert "https://" not in page


def test_source_content_cannot_close_the_page_script() -> None:
    review_aid = _actual_review_aid()
    payload = "</script><script>globalThis.compromised = true</script>"
    review_aid["cases"][0]["original_source_simulation"]["messages"][0][
        "content"
    ] = payload

    page = _render_review_page(review_aid, review_aid_sha256="b" * 64)

    assert payload not in page
    assert _embedded_review_aid(page)["cases"][0]["original_source_simulation"][
        "messages"
    ][0]["content"] == payload
    assert page.count("</script>") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_page_javascript_is_syntactically_valid(tmp_path: Path) -> None:
    page = _render_review_page(_actual_review_aid(), review_aid_sha256="c" * 64)
    match = re.search(r"<script>\s*(.*?)\s*</script>", page, flags=re.DOTALL)
    assert match is not None
    script_path = tmp_path / "normalization-review.js"
    script_path.write_text(match.group(1), encoding="utf-8")

    result = subprocess.run(
        ["node", "--check", script_path],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_page_javascript_smoke_renders_and_all_automatic_hints_pass(
    tmp_path: Path,
) -> None:
    page = _render_review_page(_actual_review_aid(), review_aid_sha256="d" * 64)
    match = re.search(r"<script>\s*(.*?)\s*</script>", page, flags=re.DOTALL)
    assert match is not None
    browser_stubs = """
const makeElement = () => ({
  textContent: "", innerHTML: "", value: "", disabled: false, className: "",
  style: {}, dataset: {},
  classList: {add() {}, remove() {}, toggle() {}},
  setAttribute() {}, addEventListener() {}, scrollIntoView() {},
  appendChild() {}, remove() {}, click() {}, matches() { return false; },
  closest() { return null; }
});
const elements = new Map();
globalThis.document = {
  body: makeElement(),
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  },
  querySelector() { return makeElement(); },
  querySelectorAll() { return []; },
  createElement() { return makeElement(); },
  addEventListener() {}
};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.confirm = () => true;
globalThis.URL.createObjectURL = () => "blob:test";
globalThis.URL.revokeObjectURL = () => {};
"""
    assertions = """
for (const caseData of audit.cases) {
  const checks = automaticChecks(caseData);
  for (const key of requiredChecks) {
    if (!checks[key].pass) {
      throw new Error("automatic hint failed for case " + caseData.case_number +
        ", " + key + ": " + checks[key].summary);
    }
  }
}
const parallelCase = JSON.parse(JSON.stringify(audit.cases.find((caseData) =>
  caseData.canonical_packet.conversation_prefix.some((message) =>
    Array.isArray(message.tool_calls) && message.tool_calls.length > 1))));
const parallelCall = parallelCase.canonical_packet.conversation_prefix.find((message) =>
  Array.isArray(message.tool_calls) && message.tool_calls.length > 1);
const firstResult = parallelCase.canonical_packet.conversation_prefix.find((message) =>
  message.role === "tool" && message.id === parallelCall.tool_calls[0].id);
const secondResult = parallelCase.canonical_packet.conversation_prefix.find((message) =>
  message.role === "tool" && message.id === parallelCall.tool_calls[1].id);
const firstId = firstResult.id;
firstResult.id = secondResult.id;
secondResult.id = firstId;
if (automaticChecks(parallelCase).tool_calls_and_results_preserved_and_paired.pass) {
  throw new Error("swapped parallel tool-result IDs were not detected");
}
const exported = buildExport();
restoreProgress(exported);
const wrongAudit = JSON.parse(JSON.stringify(exported));
wrongAudit.source_review_aid_sha256 = "0".repeat(64);
let rejected = false;
try { restoreProgress(wrongAudit); } catch (error) { rejected = true; }
if (!rejected) throw new Error("progress from a different audit was accepted");
process.stdout.write("rendered 10 cases; all 60 automatic hints pass\\n");
"""
    script_path = tmp_path / "normalization-review-smoke.js"
    script_path.write_text(
        browser_stubs + "\n" + match.group(1) + "\n" + assertions,
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", script_path],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "rendered 10 cases; all 60 automatic hints pass\n"


def test_review_aid_with_prefilled_human_decision_is_rejected() -> None:
    review_aid = _actual_review_aid()
    review_aid["cases"][0]["review"]["checks"][
        "conversation_order_matches_source"
    ] = True

    with pytest.raises(
        NormalizationReviewPageError,
        match="not a pristine assignment",
    ):
        _validate_review_aid(review_aid)


def test_review_page_rejects_tool_definitions_that_do_not_match_pin() -> None:
    review_aid = _actual_review_aid()
    for case in review_aid["cases"]:
        case["canonical_packet"]["tool_definitions"][0]["name"] += "_changed"

    with pytest.raises(
        NormalizationReviewPageError,
        match="do not match the pinned source",
    ):
        _render_review_page(review_aid, review_aid_sha256="e" * 64)
