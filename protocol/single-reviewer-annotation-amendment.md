# Single-reviewer annotation amendment

> **Approved:** 2026-09-21 by Ayush. This post-G0 amendment replaces the
> proposed two-reviewer staffing workflow for this run. It does not alter the
> G0 model scope, the $30 judge-API ceiling, the 21-case model-judgment maximum,
> or the separate 24-case annotation-only pool.

## Reason for the amendment

Ayush is the only human reviewer available for this experiment. The original
staffing proposal assumed two independent annotators and a separate
adjudicator. Those roles do not exist for this run, so inter-rater agreement,
Cohen's kappa, the 85% grounding-agreement gate, and human adjudication cannot
be performed or reported.

The original documents remain preserved as proposal history:

- `protocol/human-annotation-plan.md`
- `reviews/annotation-plan-request.md`
- `reviews/annotation-plan-decision.yaml`

This amendment and its owner decision are the current authority for human
annotation work.

## Approved single-reviewer design

| Pool | Cases | Human use | Judge calls | Included in judge comparison |
| --- | ---: | --- | ---: | --- |
| Rubric development | 24 across at least 12 task families | Ayush applies and refines the rubric before the study labels are opened | 0 | No |
| Model-judgment study | At most 21 | Ayush supplies one frozen-rubric reference label per criterion | Governed separately by G2/G3 and the $30 cap | Yes |

The pools remain case- and task-family-disjoint. Selection remains blind to
human labels, benchmark rewards, and judge outputs. The 24-case pool remains
annotation-only and cannot be used to increase the number of model-judged
cases.

## Workflow

### 0. Entry conditions and sequencing

The 24 rubric-development assignments may begin only after the separate
10-case normalization audit has an approved, append-only resolution. Exact
model-context eligibility may be resolved in parallel because the 24 cases
receive no judge calls.

### 1. Rubric-development pass

Ayush reviews all 24 rubric-development cases before opening any of the 21
study cases. For every grounding, relevance, and policy-compliance decision,
the record must contain:

- one permitted label;
- cited evidence IDs;
- a short rationale;
- an ambiguity flag;
- measured elapsed time; and
- the rubric version and hash.

Ayush may clarify the rubric in response to ambiguities found in these 24
cases. Existing annotation records are never overwritten: a changed label or
rationale is stored as a new, version-bound record. These labels are process
evidence only and are excluded from Jev-versus-GPT-5.4 accuracy calculations.

### 2. G1 rubric freeze

The experiment may proceed to study labeling only after all 24 records pass
schema and evidence-citation validation and every material ambiguity is either
resolved in the rubric or explicitly retained as a documented limitation. G1
must record the final rubric version and hash before Ayush opens the study
packets. Before that access, also freeze and hash the judge prompts, adapters,
and final request envelopes so the sole reviewer cannot tune the judges after
seeing study labels.

There is no inter-rater percentage, kappa threshold, or adjudication outcome at
this gate. A no-go occurs if the 24-case pass is incomplete, records are
invalid, evidence cannot support a reproducible decision, material ambiguities
remain undocumented, or the rubric is not frozen before study access.

### 3. Study labeling

After G1, Ayush independently labels each selected study case once under the
frozen rubric. Ayush must not inspect judge outputs while labeling. Judge
outputs, benchmark rewards, source-model identity, and other outcome-revealing
fields remain hidden until the reference-label bundle and its hash are sealed.
If Ayush cannot assign a supported label, the relevant criterion is
`UNSCORABLE`; it must never default to PASS.

There is no second annotation, consensus round, or adjudication record in this
run. The implementation may retain generic adjudication schemas for future
experiments, but this amendment does not authorize or require their use.

## Measurements retained from the 24 cases

The rubric-development pool produces:

- observed annotation time and completeness;
- counts of PASS, FAIL, `NOT_APPLICABLE`, and `UNSCORABLE` by criterion;
- ambiguity counts and examples;
- evidence-citation validation results; and
- a change log connecting observed ambiguities to the final frozen rubric.

It does **not** estimate inter-rater reliability or prove that Ayush's labels
are objectively correct.

## Claim and reporting limits

The experiment reports each model judge's **agreement with Ayush's reference
labels**. It must not describe the results as objective human accuracy, a gold
standard, independent human consensus, or independently adjudicated truth.

Because there is one reviewer and at most 21 model-judged cases, results remain
descriptive and exploratory. Reports must identify single-reviewer subjectivity
as a limitation and must not use the run to establish noninferiority or a
production-replacement claim.

## Staffing, time, and budget

- Human reviewer: Ayush only.
- Independent second annotator: not applicable.
- Adjudicator: not applicable.
- Inter-rater agreement and Cohen's kappa: not applicable.
- External annotation-labor cash budget: **$0**.
- Ayush's planning estimate remains 30 minutes total for one pass over all 45
  conversations; actual elapsed time must be captured rather than assumed.

This $0 human-labor cash budget does not change or supplement the separately
approved $30 judge-API ceiling. No model call is authorized by this amendment.
