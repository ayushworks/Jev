# G0 review request — $30 customer-support judge comparison

**Status:** pending experiment-owner decision. M0's offline implementation and
audits are complete; no support-agent or judge model was called and no spend is
authorized by this package.

## Revised decision requested

Approve the resolved GPT-5.4 proposal, approve it with edits, or return it for
revision:

| Quality anchor | Paid execution scope | Guaranteed case limit | Attempt/request limit | API ceiling | Repeatability |
| --- | --- | ---: | ---: | ---: | --- |
| `gpt-5.4-2026-03-05`, medium reasoning | Jev + GPT-5.4 judge the same final support response | **21** | 3 per judge; 126 total | **$30** | disabled |

Only the already-generated customer-support agent responses are evaluated
against their frozen customer-query evidence. The support agent is not rerun.
The five archived weather cases remain audit-only and receive no new judgment.
Human labels—not Jev, GPT-5.4, or benchmark reward—remain the reference
standard.

## Why the guaranteed maximum is 21

At the reviewed token caps and uncached Standard prices:

```text
GPT-5.4 attempt = 32,000 * $2.50/M + 25,000 * $15/M = $0.455000
Jev attempt     = 64,000 * $0.042/M                  = $0.002688
paired case with three attempts per judge             = $1.373064

floor($30 / $1.373064) = 21
21 cases = $28.834344
22 cases = $30.207408
```

Thus 21 is the largest count that is guaranteed to complete within $30 while
retaining the common three-attempt policy. A one-attempt-only policy would fit
65 worst-case cases, but is not the current proposal. Proxy-based expected
costs suggest many more cases may fit if GPT-5.4 uses only a few thousand
reasoning/output tokens, but canonical packet counts and measured usage do not
exist yet; those estimates are not spending authorization.

## Scope and statistical consequence

- Candidate pool: 456 structurally eligible retail-support trajectories from
  `claude-sonnet-4-5_sierra_2026-02-26`, frozen locally at SHA-256
  `f344a3a63783018b693f2a1a60b80b9d4f86fce6d5df74c0fcba647baacbea00`.
- Sample: at most 21 cases, chosen without human labels, judge outputs, or
  benchmark rewards after cross-task family IDs and packet eligibility are
  frozen. The grouped development/test split remains 25%/75%.
- Unit: the final customer-facing response, with frozen conversation, policy,
  tool definitions, tool calls/results, and customer request as evidence.
- Judges: Jev `jev-1.13.0` and GPT-5.4 `gpt-5.4-2026-03-05`, each receiving the
  same three criterion meanings in one batched request.
- Criteria: grounding is primary; relevance and response-policy compliance are
  secondary. Humans label each independently and are blinded to judge outputs.
- Repeatability: disabled. The former K=15 study alone has a $185.36 strict
  three-attempt envelope and cannot fit this cap.
- Inference: a roughly 15--16-case grouped test split cannot contain the
  protocol's minimum 30 human cases in either class. Results are therefore
  descriptive/feasibility findings; this run cannot establish noninferiority or
  a practical-replacement claim.
- Annotation: human labor is outside the $30 judge-API cap and requires a
  separate plan before anyone is engaged or paid.

The five-case technical pilot, if approved, is part of the 21-case maximum, not
additional work. Measured usage can support a later pre-holdout amendment; it
does not automatically expand this authorization.

## Shared technical proposal

- Provenance: accept public tau2-bench commit
  `17e07b1da2bbc0cadfddeea36412686e0604127b` as the validated compatibility
  pin while keeping the artifact's unreachable generation SHA explicitly
  unverified.
- Distribution: local research use only for the separately hosted S3
  trajectory until redistribution terms are confirmed. The Daniel Shea archive
  remains a conceptual reference because its audited pin declares no license.
- Frontier request: Responses API, `openai==3.16.2`, medium reasoning, strict
  structured JSON, no tools, no storage, Standard/default tier, truncation
  disabled, 32k input cap, 25k total reasoning/output cap, and 300-second
  attempt timeout.
- Jev request: official `typesafe-sdk==0.7.0`, pinned `jev-1.13.0`, one state
  plus three atomic Noul questions, with both the 32k state-plus-longest-question
  and 64k total-request limits enforced.
- Retry and spending: SDK retries disabled; at most three harness-owned attempts
  per judge/case; pre-dispatch reservation and an immutable usage/cost ledger;
  stop with `budget_blocked` before any call that could exceed $30.
- Recording: local artifacts are authoritative; LangSmith remains disabled.

## Evidence available for review

- [Governing protocol revision 2](../protocol/protocol-v2.md)
- [Validated upstream audit](../reports/upstream-audit.md)
- [Validated candidate-source audit](../reports/candidate-source-audit.md)
- [Source selection and provenance](../protocol/source-selection.md)
- [Upstream reuse decision](../protocol/upstream-reuse.md)
- [GPT-5.4 and Jev settings](../protocol/model-options.md)
- [Draft rubric](../protocol/rubric-draft.md)
- [Analysis and underpower plan](../protocol/analysis-plan.md)
- [$30 API budget](../protocol/budget.md)
- [Resolved GPT-5.4 proposal](../configs/g0-gpt54.yaml)
- [Hash manifest](G0-package.json)

The earlier Astra configuration remains in the repository as proposal history
but is not offered, hashed into this revised review package, or authorized.

## Artifact hashes

The complete review manifest hashes every protocol, config, audit, source
module, test, and this request. Core hashes are listed here for quick review.

<!-- HASH_TABLE_START -->
| Artifact | SHA-256 |
| --- | --- |
| [`protocol/protocol-v2.md`](../protocol/protocol-v2.md) | `0d97d298c859e8d166000cb1aaf80a62adde1a3f3474b7d45dee97a229aa5330` |
| [`configs/g0-gpt54.yaml`](../configs/g0-gpt54.yaml) | `93c06360cc5e24d93268ebdbadb22de52d715d2ca7b6c038e87439b77cb9137f` |
| [`data/manifests/upstream_archive_manifest.json`](../data/manifests/upstream_archive_manifest.json) | `25cdc4ef476ad69374127865097a7564600d4355ab8ed0acdda214cf000f829c` |
| [`data/manifests/candidate_source_manifest.json`](../data/manifests/candidate_source_manifest.json) | `f526d21ab8d1c32371996841e0404e8868b4bff61f32fb080433456211a6e157` |
| [`reports/upstream-audit.md`](../reports/upstream-audit.md) | `ecebdc673a54783e5f01109ce8dd6738acebafe974742df5a469aa6adfb51752` |
| [`reports/candidate-source-audit.md`](../reports/candidate-source-audit.md) | `bb044630d5be6d07561b31d28839e2e9d9e0b847143720214acc5bb08834ef02` |
| [`protocol/model-options.md`](../protocol/model-options.md) | `11cb155a30c75ec9ae60f2e212fd730f7c6afa40eba75073d7b7ed5cf32aeef7` |
| [`protocol/rubric-draft.md`](../protocol/rubric-draft.md) | `cefd0273ec8ca5eaa2c6a44bcfbc507f48a648a1f34589718120f2904d3707de` |
| [`protocol/analysis-plan.md`](../protocol/analysis-plan.md) | `7ab0e10ad6721c87fe1b88214d90af74e734ad36536665d1639bfa596cd34fac` |
| [`protocol/budget.md`](../protocol/budget.md) | `f7c7e6b34ce914c9320a40cd5d7c3f3160fa7b22a5298aa156c1ee553dd9b37e` |
| [`pyproject.toml`](../pyproject.toml) | `57cec0c13bddd02d41c7db50f28fb958efe58438a30cb245d84acdc1f1fe7a71` |
| [`uv.lock`](../uv.lock) | `8ac6d28efd27e2a88d8a94fb99959d73e06dbde15e01331f28177224239885db` |
<!-- HASH_TABLE_END -->

## Acceptance-check results

- Upstream: all five frozen weather cases are inventoried and remain audit-only.
- Source: 456/456 trajectories pass identity and structural validation; all
  3,220 tool calls have exactly one later result.
- Compatibility: policy and simulator guidance are byte-identical at the pin;
  full tool schema reconstruction and task-family derivation remain M1 work.
- Configuration: the revised proposal is fully typed and contains the 21-case,
  126-attempt, $30, and zero-repeatability limits. The active config remains
  pending.
- Verification: Ruff and all unit/contract tests must pass before this package
  is presented as final.
- Spend: zero model calls have been made and the decision record remains pending.

## Unresolved issues for the owner

- Approve or revise the 21-case GPT-5.4 scope, exact request settings, retry
  policy, and $30 API cap.
- Accept or reject the unavailable exact source-generation commit and the
  local-only trajectory disposition.
- Accept that the budget-limited study is descriptive and cannot support the
  protocol's formal noninferiority/replacement claim.
- Provide or approve a separate human-annotation plan; the $30 cap does not
  cover annotator labor.
- Cross-ID task families and full tool schemas do not exist yet; M1 must derive,
  review, and freeze them before sampling, splitting, or packet generation.

The active [experiment configuration](../configs/experiment.yaml) intentionally
retains `pending_g0` fields. Approval records the selected proposal before M1;
it does not bypass later G1--G3 review gates or authorize holdout execution.

## Owner response

Record **approve GPT-5.4 / $30 / 21 cases**, **approve with edits**, or
**revise** in [`G0-decision.yaml`](G0-decision.yaml). A regional-processing
endpoint or any added comparator/repeatability calls requires a revised budget.
