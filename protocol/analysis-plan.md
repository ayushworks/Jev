# G0-approved analysis plan

> **Status:** approved at G0 on 2026-09-21. Because the model-judgment sample is
> capped at 21 cases, the run is descriptive/feasibility-only; the formal
> thresholds remain documented but cannot support a noninferiority or practical
> replacement claim. Freeze the final analysis before test labels or judgments.

## Estimands and populations

The primary criterion is grounding. Human labels are the reference standard;
benchmark reward and either judge's output are not labels.

The primary estimand is trajectory-weighted performance on the natural test
set. Its analysis population is paired-valid, human-scorable grounding cases:
both judges produced valid probabilities and the adjudicated human label is
PASS or FAIL. A task-family-weighted result is a sensitivity analysis. Every
metric must state its exact denominator.

The primary fixed rule is:

```text
p_failure = 1 - p_pass
judge FAIL when p_failure >= 0.5
judge PASS otherwise
```

Consequently, `p_pass == 0.5` is a FAIL. Human `NOT_APPLICABLE` and
`UNSCORABLE` labels are reported separately and excluded from criterion error
rates. Missing or invalid judge results are never converted to PASS or FAIL.

For each judge and criterion, report confusion counts, missed-failure rate
(FNR), false-alarm rate (FPR), failure recall and precision, decision coverage,
class prevalence, Brier score, and clipped log loss. Also report paired
complete-case comparisons, per-judge valid-case results, and all-cases
operational accounting in which missing/invalid decisions route to review.

## Proposed noninferiority and practical-claim criteria

These are risk-tolerance proposals, not facts inferred from unseen labels:

| Configuration field | Proposed value | Interpretation |
| --- | ---: | --- |
| `acceptable_fnr_increase_pp` | 5 | One-sided 95% upper bound for `FNR_Jev - FNR_frontier` must be at most +5 percentage points |
| `acceptable_fpr_increase_pp` | 5 | Corresponding upper bound for FPR must be at most +5 points |
| `minimum_decision_coverage` | 0.98 | Each claimed judge result must cover at least 98% of human-scorable cases |
| `acceptable_all_cases_missed_failure_increase_pp` | 5 | Missingness cannot make Jev appear noninferior; the all-cases automatic-pass difference has the same +5-point ceiling |
| `minimum_cost_reduction_factor` | 10 | Mean complete-evaluation cost must be at least 10x lower |
| `minimum_p95_latency_reduction_factor` | 2 | Successful-call p95 end-to-end latency must be at least 2x lower |

The primary noninferiority claim passes only if both paired error-rate bounds,
coverage, and the all-cases safeguard pass. A practical replacement claim also
requires the cost and latency factors. Failure of any conjunct yields a narrower
descriptive result or an inconclusive result, not an equivalence claim.

The configured 5% missed-failure target is an operating-point target. It is not
a service guarantee. An automation-at-5% claim additionally requires the
one-sided 95% upper confidence bound for test missed-failure rate to be at most
5% and acceptable review volume.

## Paired clustered inference

Use 10,000 paired cluster-bootstrap replicates. Resample complete
`task_family_id` groups with replacement, retaining all source-agent trials and
both judges' designated primary results together. Compute the proposed
trajectory-weighted estimate in every replicate and report the task-weighted
sensitivity result separately. Do not treat four trials of one task as four
independent families.

For the headline differences, use one-sided 95% upper confidence bounds.
Ordinary two-sided 95% intervals accompany descriptive estimates. Do not infer
equivalence from a nonsignificant difference.

The source artifact supplies task IDs, not defensible variant-family IDs. The
family derivation must be reviewed and frozen before this procedure is runnable.
No cross-split family overlap is allowed.

## Pre-label feasibility and underpower rules

The revised $30 proposal caps the experiment at 21 customer-support cases. A
grouped 25% development split will leave only about 15--16 test cases, subject
to task-family sizes. It is therefore impossible for either human class to
reach the 30-case exploratory floor in the test split. Under this budget the
study is explicitly **descriptive and feasibility-oriented**: it can compare
paired judgments, costs, latency, failure modes, and calibration examples, but
it cannot support the preregistered noninferiority or practical-replacement
claim. The larger-corpus calculations below are retained to show what a future
budget amendment would need to recover.

The candidate has 456 trajectories from 114 task IDs with four trials each. A
rough 75% grouped test allocation is 340--344 trajectories before any cross-ID
family merges or exclusions. Without inspecting test labels, plausible failure
prevalences imply approximately:

| Assumed failure prevalence | Approximate test failures at n=342 |
| ---: | ---: |
| 5% | 17 |
| 10% | 34 |
| 20% | 68 |
| 30% | 103 |

The minimum exploratory rule of 30 human failures and 30 human passes per
criterion therefore requires roughly 8.8% failure prevalence for the minority
class. This floor permits interpretation of both error rates; it does not prove
adequate power.

With zero observed misses, at least 59 independent human failures are needed
for a one-sided exact 95% binomial upper bound below 5%. That corresponds to
about 17% failure prevalence before clustering. This exact calculation is a
diagnostic only because the actual design is clustered.

For a paired risk-difference margin `M`, an approximate within-class sample
requirement is `z_0.95^2 * q / M^2`, where `q` is the paired discordance rate.

| Margin | q=5% | q=10% | q=20% |
| ---: | ---: | ---: | ---: |
| 5 percentage points | 54 | 108 | 216 |
| 3 percentage points | 150 | 301 | 601 |

These counts precede cluster inflation. With four trials per cluster, the
illustrative design effect `1 + 3*rho` is 1.3, 1.9, or 2.5 for intraclass
correlation 0.1, 0.3, or 0.5. The table is a feasibility sensitivity analysis,
not a promise of achieved power. G3 must update it using development labels
only, while test labels remain sealed.

If either human class has fewer than 30 test cases, mark that criterion's
corresponding error endpoint underpowered and report counts and intervals
descriptively. Do not widen a margin, manufacture defects, add repetitions to
the primary denominator, or resample until a preferred answer appears.

## Sparse and degenerate outcomes

The following rule is proposed for advance registration:

- Report the proportion of cluster-bootstrap replicates lacking a required
  human class; do not silently discard them.
- Zero observed errors, zero paired discordances, identical paired outcomes,
  or a collapsed bootstrap distribution cannot justify zero uncertainty.
- If the bootstrap is degenerate, required classes are too sparse, or the
  one-sided bound cannot be estimated under the frozen method, the formal
  noninferiority endpoint is **inconclusive**.
- Do not choose a favorable sparse-event method after test inspection. A
  replacement method would require a pre-test amendment and a documented
  simulation or statistical justification.

This conservative rule is preferred to inventing certainty from a public
benchmark with few independent task families.

## Operational threshold analysis

The fixed 0.5 threshold remains primary. As a separately labeled secondary
analysis, choose one operational threshold per judge using development labels
only: among thresholds with empirical development FNR at most 5%, choose the
one with the lowest FPR using a preregistered deterministic tie rule. Freeze it
at G3. If no threshold qualifies, report that fact. If qualifying requires
flagging almost everything, report the resulting review burden rather than
claiming useful automation.

Raw probabilities remain the calibration input. Any fitted calibration must use
grouped development-only fitting; otherwise skip it in v1. Never reinterpret
TypeSafe's generic `confidence` as a class probability.

## Repeatability is disabled in the $30 proposal

No repeatability calls are included. The former K=15 proposal would add 135
paired case-equivalents and cannot fit the hard authorization envelope. Each
selected packet therefore receives one designated primary judgment from each
judge. Restoring repeated judgments requires an explicit budget amendment and
a revised analysis freeze; unused dollars from shorter-than-expected responses
do not silently authorize a repeatability study.

Relevance and policy compliance are secondary and descriptive unless G0
approves a multiple-comparison procedure. Stable wrong decisions are not a
reliability success.

## Freeze and disclosure requirements

Before test access, freeze the corpus and split hashes, rubric, model settings,
prompts, retry policy, threshold rules, prices, budget, repeatability prefix,
exclusions, and this analysis version. Report public-benchmark contamination,
source revision uncertainty, task-family derivation, human disagreement,
unscorable cases, missing judge outputs, and every deviation. A negative or
inconclusive result is valid.
