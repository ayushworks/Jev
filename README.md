# Jev vs. a frontier model as customer-support response judges

This repository implements protocol revision 2 of the customer-support judge
comparison. The experiment compares Jev with one reviewed frontier-model
configuration on identical, frozen support-response evidence packets. Ayush's
single-reviewer labels—not either model—form this run's reference labels.
Results therefore measure agreement with Ayush, not objective human accuracy.

The complete governing specification is preserved verbatim at
[`protocol/protocol-v2.md`](protocol/protocol-v2.md). This README is the setup
and reproduction entry point; milestone-specific decisions and deviations must
be recorded in `reviews/` and `protocol/` rather than silently changing that
baseline.

The current human workflow is the owner-approved
[`single-reviewer annotation amendment`](protocol/single-reviewer-annotation-amendment.md),
recorded in
[`reviews/single-reviewer-annotation-decision.yaml`](reviews/single-reviewer-annotation-decision.yaml).
The original two-reviewer staffing plan is retained only as proposal history.

G0 was approved by Ayush on 2026-09-21. The project is now in **M1 (corpus and
evidence-packet construction)**. No judge calls have been made and no
experiment result is claimed.

## Safety and review boundary

- `audit-upstream` inspects archived public artifacts and is prohibited from
  initializing or calling any model client.
- Local artifacts are the source of truth; LangSmith recording is optional and
  disabled by default.
- Commands that can make paid calls will require the relevant recorded human
  approval and a remaining budget before execution.
- The active configuration is byte-identical to the G0-approved dated GPT-5.4
  proposal. Paid development still requires G2; paid holdout execution requires
  G3.

## Setup

```bash
uv sync --extra dev
uv run python -m judge_compare --help
```

Validate configuration and local artifacts:

```bash
uv run python -m judge_compare validate-config
uv run pytest
```

The M0 audit and G0 packaging commands are now fail-closed because their
reviewed outputs are frozen. Do not rerun them in place after approval.

Prepare the local M1 packet corpus, grouped split, exposure exclusions, and
label-blind pool proposal with:

```bash
uv run python -m judge_compare prepare-m1 --no-model-calls
```

This command verifies the approved G0/config hashes, reads only the pinned local
source, validates all 456 canonical packets, excludes the owner-visible timing
example's entire eight-case task family (`456 = 448 + 8`), and writes
privacy-safe hashed manifests under
`data/manifests/`. Canonical packets and private source-ID traceability stay in
the ignored `data/packets/v1/` directory. The command is idempotent for
identical outputs, leaves existing identical proposal files untouched, and
refuses to overwrite drift.

The resulting 21-case model-judgment and 24-case rubric-development
selections are explicitly provisional. Exact GPT-5.4 Responses input counting
requires the complete frozen request and an explicitly authorized
`/v1/responses/input_tokens` call; exact Jev request-fit validation likewise
needs a supported counter or provider check. If either check produces any
context exclusion, work stops for an approved amendment before reselection;
there is no remaining development-family headroom.

Freeze and locally validate the complete provider request envelopes with:

```bash
uv run python -m judge_compare prepare-m1-request-fit --no-model-calls
```

This prepares one private, hash-bound GPT-5.4/Jev envelope for each of the 448
structurally eligible packets. It makes no SDK, network, model, or token-count
call and does not resolve context eligibility. The exact count step remains
fail-closed until credentials are configured and an exact, reviewed Jev
preflight counter is available.

A second G1 blocker is the protocol-required human comparison of at least 10
source trajectories with their normalized packets, covering short and long
packets plus source-success and source-failure cases. Because the named
reviewer is also a 45-case annotator, these audit examples must come from 10
distinct otherwise-unselected **test** families and must be family-disjoint
from both provisional pools and timing-exposed family 096. This prevents
source or reward inspection from contaminating measured annotations.

The immutable, content-free requirements are in
[`reviews/M1-normalization-audit-request.yaml`](reviews/M1-normalization-audit-request.yaml).
The separate
[`reviews/M1-normalization-audit-decision.yaml`](reviews/M1-normalization-audit-decision.yaml)
remains pending until a human actually completes the review. `prepare-m1`
binds only the immutable request and always records the human-audit blocker as
open at proposal time, so later approval cannot mutate the four proposal
manifests.

Prepare the deterministic private review sample and worksheet with:

```bash
uv run python -m judge_compare prepare-m1-normalization-audit --no-model-calls
```

This selects 10 packets from 10 otherwise-unused test families, balanced across
five source successes and five source failures and covering short and long
packets. It writes a private source-versus-packet review aid, a checklist
worksheet, and the bound reviewed-ID inventory under
`data/packets/v1/audits/`. The command does not mark any box, approve the audit,
or change either measured pool.

For a simpler one-case-at-a-time browser interface, generate the self-contained
private review page with:

```bash
uv run python -m judge_compare prepare-m1-normalization-review-page --no-model-calls
```

The page aligns source and normalized turns, provides explicit Correct/Wrong
controls for every required check, autosaves decisions in local browser storage,
can export and restore in-progress work, and downloads a structured private
result when the review is complete. The downloaded `PRIVATE-...json` file
contains private example IDs and source hashes; it is not the public M1 decision
and must not be committed. The page contains the private audit data inline,
makes no network requests, and remains gitignored at
`data/packets/v1/audits/M1-normalization-audit-review.html`.

Ayush, as the sole reviewer for this run, must complete the source-versus-packet
checks and keep the actual distinct example-ID inventory at
`data/packets/v1/audits/M1-normalization-audit-reviewed-example-ids.json` with
this private schema:

```json
{
  "schema_version": "1.0",
  "inventory_type": "m1_normalization_audit_reviewed_examples",
  "private_packet_corpus_manifest_hash": "<bound private manifest hash>",
  "proposal_corpus_manifest_hash": "<bound public corpus manifest hash>",
  "example_ids": ["<at least 10 private reviewed IDs>"]
}
```

After hashing that file and completing all bindings, timezone-aware signoff,
coverage, and checks in the decision record, record the approval offline with:

```bash
uv run python -m judge_compare record-m1-normalization-audit --no-model-calls
```

That command validates the signed decision, private reviewed-ID inventory,
reviewed packet hashes, blinding exclusions, and all four self-consistent
proposal manifests. It writes a separate append-only
`data/manifests/m1-normalization-audit-resolution.json`, is idempotent for an
identical resolution, refuses drift, and never rewrites the proposals. No real
resolution exists while the decision remains pending.

Exact context eligibility will likewise use a separate
`m1-context-eligibility-resolution.json`. Final M1 promotion will require both
resolution artifacts; recording the human audit alone does not finalize or
freeze M1. Public proposal and resolution manifests expose neither packet
content, reviewed IDs, nor benchmark rewards.

Under the single-reviewer amendment, the 24-case rubric-development pass may
start only after the normalization-audit resolution exists. Exact context-fit
work may continue in parallel because those 24 cases receive no judge calls.
Before Ayush opens any of the 21 study cases, G1 must freeze and hash the rubric,
judge prompts, adapters, and final request envelopes. Ayush must not inspect
judge outputs while producing reference labels.

After the signed normalization-audit resolution has been recorded, prepare the
private, randomized 24-case worksheet with:

```bash
uv run python -m judge_compare prepare-rubric-development --no-model-calls
```

The command validates the approved single-reviewer amendment and every bound M1
artifact, exposes only the 24 rubric-development packets, and writes no labels.
It deliberately fails while the normalization audit is pending. It makes no
network, token-count, or model call and does not prepare any of the 21 study
packets.

The remaining command contract is introduced milestone by milestone. Commands
must fail closed when their prerequisites, approvals, or budgets are missing.

The approved decision is recorded in
[`reviews/G0-decision.yaml`](reviews/G0-decision.yaml). It uses the dated
GPT-5.4 quality anchor, a hard $30 API ceiling, at most 21 support cases, no
repeatability calls, and a separate 24-case annotation-only calibration pool.
The exact deviations from the larger default protocol are recorded in
[`protocol/g0-approved-addendum.md`](protocol/g0-approved-addendum.md). The
earlier Astra file is retained only as proposal history. Human staffing and
labor were resolved by the single-reviewer amendment: Ayush is the sole human
reviewer, inter-rater metrics and adjudication are not applicable, and the
external annotation-labor cash budget is $0. The separate $30 judge-API cap is
unchanged.

## Layout

- `configs/` — reviewed experiment configuration
- `protocol/` — rubric, analysis plan, provenance, and reuse decisions
- `src/judge_compare/` — audit, ingest, validation, execution, and analysis code
- `data/` — immutable source material, canonical packets, and manifests
- `annotations/` — immutable human reference-label records
- `runs/` — append-only requests, responses, and cost ledger
- `reviews/` — review packages and explicit human decisions
- `reports/` — audit and experiment reports
