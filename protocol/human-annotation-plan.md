# Human annotation staffing and labor-budget plan

> **Status:** draft for owner approval. No annotator engagement, payment, or
> label production is authorized by this document. Human labor is separate from
> the approved $30 judge-API ceiling.

## Scope

The annotation workload contains two case- and task-family-disjoint pools:

| Pool | Cases | Purpose | Judge calls | Included in judge-comparison estimand |
| --- | ---: | --- | ---: | --- |
| Rubric calibration | 24 across at least 12 families | Test and refine human application of the rubric before G1 | 0 | No |
| Model-judgment study | target 21; never more than 21 | Human reference labels for the Jev/GPT-5.4 comparison | At most 126 attempts under the separate API cap | Yes |

Each case evaluates one final response from the approved customer-support agent
source using its canonical pre-response conversation, policy, and tool evidence.
At the full 21-case target, the two pools contain up to 45 unique packets. Two
independent labels per packet give **up to 90 packet-annotator assignments** and
up to 270 criterion labels before any relabeling. Selection is label-, reward-, and
judge-output-blind.

The 45 conversations above are measured-pool cases. The separate exposed
timing/training preview is an additional conversation permanently excluded from
both pools and is covered by the training allowance.

## Staffing

| Role | Count | Responsibilities |
| --- | ---: | --- |
| Independent annotator | 2 | Label all assigned packets independently; cite evidence; record a rationale and ambiguity flag; do not confer before submission |
| Senior adjudicator | 1 | Lead training; review every disagreement and grounding failure; approve clarifications; assign or facilitate final dispositions |
| Data custodian / annotation operations | 1 | Blind and randomize work; validate imports; retain immutable raw labels; seal and later release test labels |

Four people are preferred. A minimum three-person arrangement may combine the
adjudicator and custodian roles. Until the G3 release event, every person with
test-label access—including annotators, the adjudicator, and the custodian—must
be outside the implementation and prompt-tuning operator role and must maintain
the test-label boundary.

Annotators must have fluent written English, be comfortable reading structured
support records and policy text, and disclose material conflicts involving
TypeSafe, OpenAI, or the source-model submission. Compensation should be hourly,
not speed-based piecework.

## Workflow and gates

### M1 — prepare the pools

1. Derive and review task families before sampling.
2. Freeze the full label-blind grouped development/test assignment and select
   the model-judgment sample (target 21; never more than 21) from the complete
   eligible frame under the approved seed and rule.
3. Select the 24-case calibration pool from otherwise unselected development
   families, without labels, rewards, or judge outputs; prohibit case and
   family overlap. If 24 cases across at least 12 such families are unavailable,
   stop for an amendment rather than drawing from test families.
4. Freeze canonical packet, pool, family, and grouped split manifests with
   hashes.
5. Hide source-model identity, benchmark reward, expected actions, simulator
   instructions, study split, and judge information from annotators.

### Training and calibration

1. Reserve three compensated training hours per annotator for the rubric, evidence
   citations, and authored PASS/FAIL/`NOT_APPLICABLE`/`UNSCORABLE` examples.
   Teaching examples stay outside both natural pools and all metrics.
2. Both annotators independently label all 24 calibration packets, in separately
   randomized order, recording one label, evidence IDs, a short rationale,
   ambiguity, elapsed time, and rubric version for each criterion.
3. Compute pre-adjudication raw agreement and Cohen's kappa by criterion, with
   class counts. Review every disagreement and grounding failure.
4. Grounding must reach at least 85% raw agreement after any affected-item
   relabeling is performed independently. If it does not, stop at G1; a new
   calibration pool requires a separately approved amendment and budget.
5. At G1, review examples, disagreements, timing, rubric changes, and the
   measured labor forecast before freezing the rubric.

### Study labeling and custody

1. After G1, independently double-label all selected study cases (target 21;
   never more than 21).
2. Review every disagreement. Use a separate adjudicator or documented human
   consensus; unresolved cases end as `UNSCORABLE`, never default PASS.
3. Report raw agreement, kappa, class prevalence, ambiguity, adjudication, and
   unscorable rates. Finalize development labels for permitted tuning.
4. Encrypt the test-label bundle. Before release, the working repository may
   contain only its hash and non-label completeness/agreement summaries.
5. Release test labels only after G3 approval and after holdout judge outputs
   and the request ledger are complete and hashed. Record the release event.

## Data handling

The source is a public synthetic benchmark, but packets can contain
identity-like fields such as names, addresses, email addresses, payment
fragments, and order IDs. Treat packets as confidential research records:

- use a controlled local workspace or custodian-controlled remote environment
  with no packet download or copy-out;
- do not use consumer AI tools, public annotation platforms, personal cloud
  drives, screenshots, or portable raw-trajectory copies;
- do not move packets outside the approved local-research environment;
- require confidentiality, conflict-of-interest, and independence
  acknowledgements;
- preserve each annotator's raw submission separately from adjudicated labels;
- keep test labels encrypted and outside the implementation operator's access
  until release; and
- remove temporary working copies after G4 under the eventual retention policy.

## Labor estimate

The source packet-length proxy averages about 9,642 tokens and has a p95 of
13,044. Ayush estimates **30 minutes total for one pass over all 45
conversations** (40 seconds per conversation), normalized in
`reviews/annotation-timing-estimate.yaml`. This is a planning estimate, not an
observed timing measurement. It applies only to Ayush's pass; until the second
annotator is timed, retain the conservative, unobserved allowance of **30
minutes per conversation** for that person. Calibration records actual elapsed
time for both annotators.

| Work | Hours |
| --- | ---: |
| Ayush: up to 45 assignments, owner estimate | 0.5 |
| Second annotator: up to 45 assignments × 0.5 hour | Up to 22.5 |
| Training: 3 hours × 2 annotators | 6 |
| Independent affected-item relabeling and QA allowance | 6 |
| Adjudication, grounding-failure review, and gate audit | 10 |
| Custody, randomization, import validation, sealing, and release | 8 |
| **Base estimate** | **53** |
| Routine contingency (20%) | 10.6 |
| **Maximum combined labor authorization, rounded up** | **64 hours** |

At the full 21-case target, the base estimate allocates 6.5 hours to Ayush
(0.5 labeling, 3 training, and 3 relabeling/QA), 28.5 hours to the second
annotator, 10 to the adjudicator, and 8 to the custodian. The 20% reserve gives
a combined 63.6 hours, rounded up to a 64-hour ceiling. Ayush's time has zero
cash cost in the table below but must still be reported as research labor and
may later receive an explicit opportunity-cost valuation.

Illustrative loaded-rate scenarios:

| Scenario | Second-annotator rate | Adjudicator rate | Custodian rate | Base cash cost | Cash ceiling with reserve |
| --- | ---: | ---: | ---: | ---: | ---: |
| Lean/internal | $35/h | $50/h | $45/h | $1,857.50 | **$2,250** |
| Experienced research staff | $50/h | $75/h | $60/h | $2,655 | **$3,200** |
| Specialist | $75/h | $110/h | $90/h | $3,957.50 | **$4,750** |

The ceilings are 120% of each base cost, rounded up to the next $50:
$2,229 to $2,250, $3,186 to $3,200, and $4,749 to $4,750.

The recommended authorization is the experienced-research-staff scenario,
capped at **$3,200 cash and 64 combined hours**. These are dual ceilings, not spending targets.
The ceiling excludes annotation-platform and recruiting fees. Approval permits
staff engagement plus training and calibration after M1; study labeling may
begin only after G1. At G1, replace the 30-minute assumption with observed
median and p90 times and re-estimate before any study labels are produced. Paid
development judge calls remain prohibited until G2, and paid holdout calls
until G3. If either labor ceiling is forecast to be exceeded, pause for review
rather than removing independent labeling, evidence citations, or adjudication.

Internal or volunteer labor may have zero cash spend, but its hours and an
illustrative opportunity cost must still be reported. No second calibration
round is included in the contingency.
