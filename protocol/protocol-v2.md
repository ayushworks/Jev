# Experiment: Jev vs. a frontier model as customer-support response judges

**Status:** implementation specification; no experiment has been run.  
**Prepared:** 2026-09-20.  
**Protocol revision:** 2 — extension of Daniel Shea's `jev-as-a-judge` experiment.  
**Audience:** an implementation agent working with a human experiment owner.

Build on [Daniel Shea's Jev-as-a-judge experiment using LangSmith](https://github.com/danielgshea/jev-as-a-judge) to compare Jev and one frontier language model as judges of customer-support responses. Reuse its evaluator integration and frozen-input approach, then measure failure detection on a broader, independently labeled support corpus. Measure accuracy, repeatability, complete-evaluation cost, and latency separately. A negative or inconclusive result is a valid outcome.

This README specifies the work to implement. Commands and project files below are proposed interfaces, not existing software. It does not authorize spending, invent results, or select a frontier model on the owner's behalf; those decisions are resolved at the first review gate.

**First action for the implementation agent:** audit the pinned upstream code and archived results without model calls, complete M0's support-source investigation, and prepare the G0 review package with a reuse map and recommended configuration. Present concrete choices and estimates for review before paid execution.

## 0. Prior work and the extension we will implement

### What the existing experiment establishes

Audit upstream commit [`adfea74905f721ea2594e22804c8c8edf1693163`](https://github.com/danielgshea/jev-as-a-judge/tree/adfea74905f721ea2594e22804c8c8edf1693163). Its published experiment ID is `6d08df72-c878-458c-b7c5-a7824ee6e721`.

The [upstream README](https://github.com/danielgshea/jev-as-a-judge/blob/adfea74905f721ea2594e22804c8c8edf1693163/README.md) describes five frozen weather-agent responses judged 100 times each, with one human reviewer. It reports Jev agreeing with all binary labels, low score variation, and low latency. Those results motivate this extension; they are not results from our support experiment.

The [archived human labels](https://github.com/danielgshea/jev-as-a-judge/blob/adfea74905f721ea2594e22804c8c8edf1693163/assets/benchmark-jev-luna-terra-sonnet/6d08df72-c878-458c-b7c5-a7824ee6e721/oracle-labels.json) mark every response grounded and useful. Only Dublin fails overall because it asks for clarification instead of performing a required search. There are no human-labeled grounding failures in that corpus. Five responses repeated 100 times remain five distinct cases for generalization.

### Reuse map

Keep upstream provenance and preserve code attribution. Verify reuse terms before copying modules. Record any licensing or dependency limitation and propose a concrete alternative at G0; it must not silently change the scientific task.

| Upstream component | What to reuse | Required adaptation |
| --- | --- | --- |
| `src/evals/judges/system_one.py` | Jev questions over shared structured state | Pin the model; replace weather questions with the approved support criteria; retain raw per-criterion probabilities |
| `src/evals/judges/llm.py` | Structured-output frontier evaluator | Use the same atomic criteria and request per-criterion probabilities; specify reasoning and output settings |
| `src/evals/judges/__init__.py` | One evidence builder shared by judges | Preserve full preceding conversation, tool arguments, tool results, and support policy; enforce the packet allowlist |
| `src/evals/judge_reliability.py` | Frozen inputs, repeated judging, case-aware statistics | Load archived inputs; add grouped splits, durable results, explicit repetition IDs, budget controls, and separate primary/repeatability analyses |
| `analysis/` and archived assets | Analysis organization and reference results | Add human failure-detection metrics, missing-result accounting, and full-evaluation cost/latency |
| LangSmith traces and experiments | Optional inspection and comparison UI | Persist complete local inputs, outputs, and ledgers first; make LangSmith recording an explicit configuration choice |

The upstream [Jev evaluator](https://github.com/danielgshea/jev-as-a-judge/blob/adfea74905f721ea2594e22804c8c8edf1693163/src/evals/judges/system_one.py) averages probabilities, while its [LLM evaluator](https://github.com/danielgshea/jev-as-a-judge/blob/adfea74905f721ea2594e22804c8c8edf1693163/src/evals/judges/llm.py) averages generated ratings. The latter already uses typed Pydantic schemas. Our comparison must therefore use matching criterion meanings and must not claim that structured output is unique to Jev. Do not carry over the broad overall-pass prompt or use averaged quality as the retail primary endpoint.

### Three distinct work products

1. **Upstream archive audit:** inspect the five stored cases, labels, analysis, and settings. Independently verify recoverable numbers and identify missing raw data. No weather-agent or judge API calls are required.
2. **Primary customer-support study:** one designated judgment per judge on each frozen holdout response; compare against independent criterion-level human labels.
3. **Secondary repeatability study:** fresh repeated judgments on a small, preselected subset of those same support responses. Measure variation and consistently wrong decisions without increasing the primary sample size.

The [upstream benchmark runner](https://github.com/danielgshea/jev-as-a-judge/blob/adfea74905f721ea2594e22804c8c8edf1693163/src/evals/judge_reliability.py) generates new weather-agent outputs when invoked again. Running it is not an exact replay of the published archive. Do not invoke it as an offline reproduction step. Any paid weather replication is optional, separately budgeted, and clearly distinguished from archival verification.

## 1. Research question and initial scope

**Primary question:** How well does each judge detect unsupported factual or completion claims in a customer-support agent's response, relative to independently adjudicated human labels?

**Secondary questions:** How do they compare on response relevance, response policy compliance, probability calibration, repeated-judgment consistency, invalid outputs, cost, and latency? Can either judge support useful automation at a specified missed-failure rate?

The primary statistical comparison is **grounding FNR and FPR at the fixed 0.5 failure threshold, on the same human-scorable cases with valid decisions from both judges**. Any primary noninferiority claim must also pass the approved coverage and all-cases automatic-pass safeguards. Development-selected thresholds are a secondary operational analysis; do not choose the more favorable analysis after seeing test results.

Use the following proposed starting setup. Freeze the final choices at G0.

| Component | Proposed choice | Reason |
| --- | --- | --- |
| Customer-support environment | Sierra's `tau2-bench`, retail domain, pinned revision | Existing policies, tools, tasks, and published agent runs |
| Support agent | The upstream standard `LLMAgent` using an existing published model configuration | Avoid introducing a custom agent implementation |
| Initial response source | A published standard retail run; investigate the Claude Sonnet 4.5 submission first | Reuse existing trajectories instead of paying to generate responses |
| Judge A | A versioned Jev model; `jev-1.13.0` is the initial candidate | Evaluate Jev directly through its official API or SDK |
| Judge B | One explicitly selected frontier model snapshot from a different family than the response-generating model, where practical | Reduce self-preference as a confound |
| Evaluation unit | One final customer-facing response from each eligible completed trajectory, with preceding conversation and tool evidence | A clear, reproducible response-level task |
| Reference labels | Two independent human annotations per response and criterion, with adjudication | Neither competing judge defines the answer key |
| Initial language | English | Keep the first experiment focused |
| Repeatability sample | Target 20 holdout packets from 20 distinct task families; 10 total judgments per packet and judge | Measure stability on unchanged support evidence without repeatedly scoring the whole corpus |
| Recording | Local artifacts required; LangSmith optional | Reproduce analysis without a hosted workspace or new API calls |

The public [standard agent implementation](https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/agent/llm_agent.py) and [agent guide](https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/agent/README.md) define the scaffold to use. This is a benchmark agent, not Sierra's proprietary production agent. Do not use a ground-truth-assisted agent such as `LLMGTAgent` to generate the natural evaluation corpus.

The repository has evolved beyond its original release. Resolve the benchmark, policies, and source run to compatible versions; a link to `main` is a discovery link, not a reproducibility pin. Start with the [submission manifest](https://github.com/sierra-research/tau2-bench/blob/main/web/leaderboard/public/submissions/manifest.json) and the [candidate submission metadata](https://github.com/sierra-research/tau2-bench/blob/main/web/leaderboard/public/submissions/claude-sonnet-4-5_sierra_2026-02-26/submission.json). Actual trajectory downloads and their completeness still need verification.

## 2. Rules that keep the comparison valid

1. **Evaluate the judges on the same frozen examples.** Do not let either judge change the support agent's actions or generate a different corpus.
2. **Keep task success separate from response quality.** A failed refund can be accurately explained; a successful refund can be described incorrectly. Benchmark reward is an auxiliary outcome, not a human response-quality label.
3. **Use independent human labels.** Humans must not see Jev outputs, frontier outputs, model identities, or benchmark reward while assigning response-quality labels.
4. **Give both judges the same evidence and criteria.** Allow API-specific formatting and model-appropriate settings, but freeze the semantic rubric and evidence packet.
5. **Split by task family.** All repetitions, related variants, and later source-model runs of the same benchmark task belong to the same split.
6. **Tune only on development data.** No test labels or test judge outputs may influence prompts, thresholds, exclusions, or model selection.
7. **Account for every example and request.** Record exclusions, missing labels, abstentions, invalid outputs, retries, failures, and costs. Do not silently drop unfavorable cases.
8. **Do not assume typed output means correct judgment.** Measure decision accuracy and output validity separately.
9. **Separate repetitions from examples.** The designated primary judgment alone enters primary accuracy and latency/cost metrics. Repeated judgments have their own dataset, budget, and analysis.

The upstream [evaluation specification](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md) explains that retail's default reward combines database-state and required-communication checks. Reference actions can describe one valid solution without being the only valid path. LLM-based assertion scores, if present, must not become independent ground truth for this comparison.

## 3. Milestones and human review gates

Finish all artifacts for a gate before requesting review. A gate is a review of a concrete package, not a request for permission to start planning.

| Milestone | Deliverables | Measurable completion criteria | Human review |
| --- | --- | --- | --- |
| M0 — Lock the protocol | Upstream archive audit, reuse map, candidate source audit, model settings, budgets, analysis plan | Five upstream cases and label distributions accounted for; recoverable claims checked; every configuration field resolved; spending caps and claim thresholds specified | **G0:** approve reuse, scope, sources, costs, and proposed claims |
| M1 — Freeze the corpus | Source manifest, normalized packets, split manifest, repeatability sample, exclusion log | 100% of included packets validate and have hashes; zero duplicate IDs or task-group overlap; corpus counts reconcile; repeatability selection uses no judge outputs or labels | Included in G1 |
| M2 — Calibrate the rubric | Rubric, 24 pilot annotations from at least 12 development task groups, disagreement report | Two independent annotations per pilot item and criterion; every disagreement reviewed; primary raw agreement at least 85%, or revise and repeat | **G1:** approve corpus, splits, rubric, and annotation examples |
| M3 — Build gold labels and adapters | Full human labels, adapted evaluators, reuse/change log, validation report | Every item has two labels and a final disposition per criterion; no unresolved scored labels; all contract tests pass, including repeat/resume behavior | **G2:** approve label quality, adapted requests, and adapter fairness |
| M4 — Run the development pilot | Paired development outputs, cost audit, frozen prompts, thresholds, and repetition schedule | Every development request accounted for; no evidence mismatches; both studies' configurations frozen; approved budget covers primary and extra calls | **G3:** approve holdout and bounded repeatability run |
| M5 — Execute both locked studies | Primary and repeatability outputs, request ledger, run manifest | Every planned packet × judge × repetition has a terminal status; identical packet hashes; one primary record per packet/judge; zero unapproved configuration changes | Escalate only material deviations |
| M6 — Analyze and review | Report with separate archive, primary, and repeatability results | Every new metric reproduces offline; original reported figures clearly attributed; uncertainty, stable errors, costs, limitations, and inconclusive endpoints explicit | **G4:** approve conclusions and any external publication |
| M7 — Optional confirmation | Second source-model or domain experiment | New preregistered scope and grouped split; same reporting standard | Separate approval before expansion |

Milestone completion does not require Jev to win. If available data cannot support the intended statistical claim, record that limitation and complete an exploratory comparison, or obtain approval for additional independent data.

## 4. M0 — Protocol and source verification

### Tasks

Before the support-source tasks, complete `reports/upstream-audit.md` and `protocol/upstream-reuse.md`:

- Read the pinned upstream files in the reuse map. Record repository revision, package versions, published experiment ID, archive URLs, and hashes.
- Inventory the five frozen weather cases and label distribution per criterion. Confirm that repeated decisions are not counted as independent cases.
- Check reported binary accuracy, variance definitions, and cost/latency units against available artifacts. Distinguish independently recomputed results from quoted summaries. If raw repeated scores or billing records are unavailable, document exactly which claims cannot be independently reproduced; do not regenerate fresh data and call it the original result.
- Document original model/prompt settings and gaps, including the unreported hosted Jev version. Explain every intentional change to our adapters, evidence, rubric, and metrics.
- Define local recording as the source for reproduction. If LangSmith or a gateway is selected, record its configuration, pricing basis, and the artifacts it receives in the G0 package.

Then investigate the support corpus:

1. Inspect the benchmark documentation, submission metadata, trajectory download links, license, and version history.
2. Identify the exact source agent, simulator, prompt modifications, trial count, seeds, and benchmark revision. Preserve originals and record anything unknown.
3. Download a small source sample and verify that conversations, tool calls, tool results, and termination metadata are available. Do not assume advertised trajectory availability means a usable download exists.
4. Prefer an intact published standard run. If unavailable, propose another compatible published run. If generating trajectories is necessary, prepare a pinned upstream configuration and separate cost estimate for human approval; do not silently substitute a custom agent.
5. Select the frontier judge and its exact model version, reasoning setting if supported, output budget, sampling settings, timeout, and price source. Use a credible configuration. Include an inexpensive frontier candidate in the selection rationale: the upstream cost difference between Jev and Luna was much smaller than the difference against Sonnet. Do not add another paid comparator without budgeting it.
6. Set primary claim criteria and separate primary/repeatability budgets before judging. Reserve most evaluation spend for distinct support examples; fit the repetition study within the approved cap by reducing its size at G3 if necessary.

### Required configuration

Create `configs/experiment.yaml` with at least the following fields. This is a schema sketch; replace every `null` with a reviewed value or documented `not_applicable` before G0 passes.

```yaml
experiment_id: retail-response-judges-v1
protocol_revision: 2
upstream:
  repository: https://github.com/danielgshea/jev-as-a-judge
  commit: adfea74905f721ea2594e22804c8c8edf1693163
  experiment_id: 6d08df72-c878-458c-b7c5-a7824ee6e721
  audit_mode: archived_no_model_calls
benchmark:
  repository: https://github.com/sierra-research/tau2-bench
  commit: null
  domain: retail
  source_submission: null
  source_agent_model: null
  source_simulator_model: null
  source_run_revision_verified: null
unit: final_customer_facing_response
split:
  group_key: task_family_id
  development_fraction: 0.25
  seed: 20260920
judges:
  jev:
    model: jev-1.13.0
    sdk_version: null
  frontier:
    provider: null
    model: null
    sdk_version: null
    reasoning_setting: null
    sampling_settings: null
    max_output_tokens: null
execution:
  primary_concurrency: 1
  scheduling_seed: 20260922
  timeout_seconds: null
  maximum_attempts: 3
  maximum_paid_requests: null
  budget_usd: null
  price_snapshot_date: null
repeatability:
  enabled: true
  target_packets: 20
  max_packets_per_task_family: 1
  total_judgments_per_packet_per_judge: 10
  primary_repetition_id: 0
  selection_seed: 20260921
  selection_basis: task_family_and_packet_length_without_labels
  additional_budget_usd: null
  maximum_share_of_total_judging_budget: 0.25
recording:
  local_artifacts_required: true
  langsmith_enabled: false
analysis:
  primary_criterion: grounding
  primary_population: paired_valid_human_scorable
  primary_threshold_analysis: fixed
  fixed_failure_threshold: 0.5
  target_missed_failure_rate: 0.05
  acceptable_fnr_increase_pp: null
  acceptable_fpr_increase_pp: null
  minimum_cost_reduction_factor: null
  minimum_p95_latency_reduction_factor: null
  confidence_level: 0.95
  cluster_bootstrap_replicates: 10000
  minimum_decision_coverage: null
  acceptable_all_cases_missed_failure_increase_pp: null
```

The 5% missed-failure target is a proposed operating point, not a certified service guarantee. Choose practical tolerances with the owner; distinguish statistically supported claims from descriptive results.

Use the entire eligible matching retail run unless the approved budget requires a random sample. Report distinct task groups as well as trajectory counts. Repeated trials are correlated observations, not additional independent tasks. Before G0, estimate whether the available groups and plausible failure prevalence can support the desired uncertainty bounds; use a range of prevalence assumptions, not unseen test labels. Update the feasibility assessment using development labels at G3 without looking at test labels.

As a minimum exploratory reporting rule, require at least 30 human failures and 30 human passes per criterion in the test set to interpret both error rates beyond descriptive counts. This does **not** establish adequate statistical power. If a class is smaller, report its count and interval and mark that endpoint underpowered. Do not manufacture failures or resample until a preferred answer appears.

**G0 package:** upstream archive audit and reuse map, protocol, filled configuration, source sample, provenance gaps, primary/repeatability API budgets, annotation effort, and analysis/claim criteria. The total spending cap includes all studies and retries; the repeatability allowance is not permission to exceed that cap.

Calculate the 25% repeatability share against **planned judge-API spending for development + primary + additional repetitions**, including retry allowances. Exclude annotation, support-agent generation, optional weather replication, and platform fees from this denominator. Do not use an inflated, unused authorization ceiling to make the share appear smaller.

## 5. M1 — Build immutable evidence packets

### Normalize and split

- Preserve downloaded files unchanged under `data/raw/`; record their URLs, retrieval timestamps, licenses, sizes, and SHA-256 hashes.
- Assign stable `task_family_id`, `task_id`, and `trajectory_id`. Group known variants together; document how family grouping was derived. A family means a shared underlying scenario or derivation, not every task with the same broad action such as refunds.
- Use a seeded group split: approximately 25% development and 75% test. Preserve natural outcome prevalence. If stratifying, use only preregistered source metadata and retain the grouping constraint.
- Select one final customer-facing response from each eligible completed trajectory using a deterministic rule. Ignore terminal control tokens. Exclude interrupted trajectories without a usable final response, and record the reason. Do not count an earlier intermediate response as a final answer to an unfinished task.
- Keep all preceding user messages, agent messages, and paired tool calls/results in order. Remove only clearly identified hidden reasoning and control metadata under one documented rule.
- Include the compatible retail policy and tool descriptions needed to interpret results. Preserve source message and call IDs so reviewers can trace evidence.
- Build one canonical packet and reuse it for humans and both judges. Agent/source-model identifiers belong in the private manifest, not the packet.

### Packet contract

Each packet must contain `schema_version`, opaque `example_id`, `policy`, `tool_definitions`, `conversation_prefix`, and `target_response`. Store split/group/provenance metadata separately. Hash a canonical serialization and log this `packet_hash` with every judgment.

The packet must not contain benchmark reward, hidden user-simulator instructions, expected solution actions, evaluator output, human labels, another judge's output, or future messages. Apply a field allowlist when exporting packets; blacklisting a few known label keys is insufficient.

Both judges receive the same evidence content. Different tokenizers and API wrappers are expected; record them. If a packet cannot fit either model's approved limits, exclude it from **both** primary inputs and report it as a context exclusion. Do not silently truncate, summarize with an LLM, or give one judge extra records. A long-context extension requires a separately reviewed protocol.

Jev's documented limits currently distinguish the total request from state plus the longest question; the implementation must validate both. Use a pinned model and confirm limits at setup. See the official [model reference](https://docs.typesafe.ai/models).

### Acceptance checks

- Every included source trajectory maps to exactly one primary packet.
- Source total = included + excluded, with one primary exclusion reason per excluded trajectory.
- Zero cross-split task-family overlaps, duplicate packet IDs, and forbidden fields.
- No unmatched tool calls/results without an explicit reviewed disposition.
- A human checks at least 10 normalized packets against originals, covering short/long conversations and successful/failed benchmark tasks where available.
- Freeze `source_manifest.json`, `split_manifest.json`, and `corpus_manifest.json` with hashes and counts.

### Preselect the repeatability subset

Before any test labels or judge outputs are inspected, deterministically select up to 20 test task families with the configured seed, and one eligible packet per selected family. Use a documented short/medium/long packet-length stratification to cover input sizes; record the full selection order and any deviations from proportional sampling. Do not select examples because judges disagree on them or because their eventual scores are near a threshold.

Save `repeatability_manifest.json` with example IDs, task-family IDs, packet hashes, length bins, sampling method, and the planned repetition count. If fewer than 20 test families exist, use all available families and flag the reduced scope. An approved budget reduction at G3 must use the predetermined order or a predefined allocation within bins; never choose which cases to retain using test outcomes.

## 6. M2–M3 — Establish human reference labels

### Rubric

Use separate binary decisions rather than an ambiguous overall quality score. Humans can additionally select `NOT_APPLICABLE` or `UNSCORABLE`; judges are compared on the same criterion-specific scorable set.

| Criterion | PASS | FAIL | Scope |
| --- | --- | --- | --- |
| **Grounding — primary** | All material factual and completion claims in the target response are supported by the visible conversation, policy, or tool results | At least one material claim is contradicted or lacks required support; e.g. claiming a refund completed when no successful refund result exists | Target response; preceding evidence establishes support |
| Response relevance — secondary | Addresses the current customer request, or accurately explains a limitation, refusal, escalation, or necessary next step | Materially ignores, misstates, or evades the current request | Target response in context |
| Response policy compliance — secondary | The response's statements, advice, and commitments are consistent with the provided policy | The response itself asserts or recommends a prohibited action, misstates policy, or makes a prohibited commitment | Response content; whole-trajectory action compliance is a separate optional study |

For grounding, use `NOT_APPLICABLE` if there is no material verifiable claim, such as a bare greeting. Missing evidence because of a broken export is `UNSCORABLE`; absence of a required tool result or equivalent confirmation in an otherwise complete trace can itself justify a grounding failure. Define this distinction with examples before annotation.

Example: the tool reports that a refund failed. “Your refund has completed” fails grounding. “The refund failed; I can explain the next step” can pass grounding and relevance even though the underlying support task was not completed.

### Annotation procedure

1. Train two human annotators on the rubric using development examples only. Randomize example order and hide judge/model/reward metadata.
2. Independently label a pilot of 24 development packets spanning at least 12 task groups. Include contrasting supported/unsupported examples if present. Any authored teaching examples stay outside the natural corpus and reported metrics.
3. Record criterion label, cited evidence IDs, brief reason, and ambiguity flag. Do not ask a competing model to prelabel or settle disagreements.
4. Calculate raw agreement and Cohen's kappa per criterion on pre-adjudication labels. Report class prevalence; kappa can be unstable for rare failures.
5. Review all pilot disagreements. Revise ambiguous definitions, then relabel affected pilot items independently. Require at least 85% raw agreement for the primary criterion before G1; document secondary agreement and any dropped criterion.
6. After G1, freeze the rubric. Independently double-label every retained development and test packet. Use an additional human adjudicator or documented human consensus to settle disagreements. Unresolved cases remain `UNSCORABLE`, never default PASS.
7. Report full-corpus pre-adjudication agreement, adjudication counts, and exclusion rates. If a rubric flaw appears during full annotation, stop and version the correction; relabel all affected cases before model evaluation.

The 85% agreement target is a rubric-clarity screen, not proof of label accuracy. G1 must also review failure-class disagreements and supporting evidence. If the natural pilot contains no failures, use separate labeled teaching examples to check annotator understanding of failure cases; keep them out of corpus metrics.

**G1 package:** corpus audit, split manifest, rubric, pilot labels, disagreement examples, and annotation effort estimate.

Keep test labels under a human/data custodian's control until G3 approves the frozen run. The tuning agent may use development labels. Test-label summaries supplied before G3 should be limited to annotation completeness and agreement audits, not model-selective error analysis. If separate personnel are unavailable, document the weaker blinding and enforce a process boundary: no test-label inspection during development.

**G2 package:** annotation audit, sealed test-label hash, final rubric hash, adapter comparison, and validation results. Acceptance requires a final disposition for every item/criterion and explicit review of any large unscorable or disagreement rate.

Include a criterion-level failure-coverage audit. For development, explicitly count supported claims, unsupported claims, incorrect amounts/statuses, and false completion claims where observed. For the sealed test set, the label custodian records these counts for release during analysis; the tuning agent does not inspect them. If a criterion has no human-labeled failures, it cannot support a claim about detecting that failure. Source-task reward failures do not satisfy this requirement by themselves. Do not add authored defects to the natural holdout to fill the gap; use the separately reported diagnostic extension in Section 11.

## 7. M3 — Implement interchangeable judge adapters

Implement Python adapters behind one interface:

```python
judge(packet, rubric, model_config) -> JudgeResult
```

Adapt the upstream evaluator structure rather than building an unrelated harness. Reuse permitted modules where useful and document modified or replaced components in `protocol/upstream-reuse.md`. The Jev integration may use the upstream LangChain wrapper if it exposes the required model pin, raw probabilities, usage, and retry controls; otherwise use the official SDK behind the same interface. Apply the same rule to frontier API/gateway integration. This experiment does not depend on `typed_evals`.

Both judges must evaluate the exact same proposition for each criterion. For example, use the approved meaning of “all material claims in this response are supported” for both, instead of asking one for a degree of quality and the other for a probability. Generate API-specific prompts from one versioned criterion definition. Do not infer overall PASS by copying the upstream broad prompt, and do not average unrelated criterion scores into the primary result.

Local recording is mandatory. LangSmith tracing/experiment upload is optional and disabled by default; enable it only within the reviewed configuration. Store correlation IDs when enabled, but ensure analysis, resumability, and cost accounting work from local artifacts without LangSmith access.

### Jev adapter

- Send the canonical packet as shared state and one atomic binary question per criterion. Use the official binary probability primitive, or an equivalent two-option question whose PASS probability is unambiguous.
- Batch the independent criteria in one request where supported. Do not treat their errors as statistically independent.
- Preserve raw outputs and extract `p_pass` for each criterion. Validate field mapping against the pinned SDK documentation before running.
- Do not substitute the SDK's generic `confidence` value for `p_pass`. TypeSafe distinguishes class probabilities from distribution-based confidence in its [confidence documentation](https://docs.typesafe.ai/confidence).

### Frontier adapter

- Give the model the same evidence, criterion definitions, and approved few-shot examples, if any.
- Request structured JSON with `p_pass` in `[0,1]` for each criterion. These are elicited probabilities; they are not assumed to have the same calibration properties as Jev's outputs.
- Permit the chosen model's normal reasoning capability under the approved settings. Do not require visible explanations in the primary comparison; do not disable reasoning merely to mimic Jev.
- Use provider-supported structured output when available. Count invalid responses after the agreed parser/retry policy; do not silently repair them with another model.

### Shared result contract

Store `example_id`, `packet_hash`, `rubric_hash`, `config_hash`, `study_track`, `repetition_id`, judge name, requested and returned model IDs, raw-response location, and per-criterion `p_pass`, `p_failure = 1 - p_pass`, and decision. Also store request IDs, timestamps, full-evaluation latency, token usage, cost basis, attempts, and terminal status. Aggregate every underlying API call used for the criterion bundle into the logical evaluation record while preserving per-call detail.

Use `study_track=development` for development calls, and `study_track=primary` with `repetition_id=0` for the one designated test judgment per judge and packet. Selected packets reference this existing result as their first repeatability observation; do not copy it into the ledger as a second paid call. Additional observations use `study_track=repeatability` and repetition IDs 1 through 9 for the default 10-total schedule. Source-agent trial IDs, judge repetition IDs, and transport retry attempts are three different identifiers.

Allowed terminal statuses: `ok`, `timeout`, `rate_limited`, `invalid_output`, `provider_error`, or `budget_blocked`. Preserve partial criterion results without pretending the whole request succeeded. Missing probabilities are missing results, not zero-probability predictions.

Both adapters should treat conversation content as evidence, not as instructions for the evaluator. Apply equivalent protections against instructions embedded in the transcript. Do not silently strip difficult examples.

### Required verification

Use targeted tests for risks that could invalidate the comparison:

- Packet equivalence and allowlist checks catch labels/reward accidentally entering either adapter.
- Split validation catches repeated task IDs or variants crossing development/test boundaries.
- Known mocked probabilities produce correct PASS/FAIL mapping, including exact threshold boundaries.
- Invalid JSON, missing fields, out-of-range probabilities, timeout, and rate-limit cases produce visible terminal statuses and correct attempt counts.
- A restart resumes only missing work and cannot silently duplicate completed calls or merge different model/rubric versions. Its key includes study track and repetition ID, so a new repeat makes a fresh call and retrying that repeat does not create a new statistical observation.
- Repeatability fixtures verify that the designated primary judgment is counted once, extra repeats never enter primary metrics, and all planned repetitions have recorded outcomes. The repeat runner loads frozen packets without invoking the support agent or external tools.
- Hand-calculated metric fixtures verify false-negative/false-positive denominators, missing-result handling, and paired group resampling.
- Review the actual serialized requests for at least five packets before paying for a development batch.

## 8. M4 — Development pilot and test freeze

Run both judges on the development split only after G2. Start with five paired packets to check integration, then continue within the G0 budget. Persist calls immediately so interruptions do not lose results.

Use the development split to improve prompt clarity and resolve implementation bugs. Keep a prompt/configuration change log, report the number and cost of trials, and give both judge configurations a reasonable documented tuning opportunity. Do not select settings using test outcomes.

Freeze two analyses:

1. **Fixed threshold:** classify a failure when `p_failure >= 0.5` for both judges.
2. **Operational threshold:** for each judge separately, select a development threshold with empirical missed-failure rate at most the approved target, choosing the lowest false-alarm rate among eligible thresholds. Prespecify deterministic tie handling. Freeze it before test evaluation. If this requires flagging nearly everything, report that lack of useful discrimination.

A development target does not certify the same rate on unseen data. Test uncertainty determines the supported claim. Any probability calibration must be fitted exclusively within development using grouped cross-validation or a separate grouped calibration subset; preserve raw-probability results as the primary calibration analysis. Skip fitted calibration in v1 if the development sample is too small.

At G3 freeze and hash: upstream reuse/change record, code revision and lockfile, evidence packets, splits, repeatability subset and schedule, rubric, prompts, model settings, thresholds, exclusion rules, request scheduling, retries, price snapshot, statistical analysis, and claim criteria. Confirm the extra repetition cost fits the reviewed allowance and total cap. The test-label custodian releases labels to the analysis stage only after this freeze; generation of requests and completion of test calls do not require label access.

**G3 package:** development results, all unresolved issues, revised power/precision assessment from development data, both studies' planned call counts and costs, remaining budget, repeatability manifest, and immutable run manifest. Do not run the holdout or its repetitions until approved. Reduce repeatability size before the freeze if necessary to keep additional repetition spend within 25% of the total judging budget; record the revised counts and retain a meaningful budget for distinct examples.

## 9. M5 — Locked primary and repeatability execution

### Primary holdout

- Run both judges on every eligible test packet, including packets later labeled not applicable for a particular criterion. Human applicability labels must not determine either judge's requests.
- Use an identical workload and approved concurrency. For the primary latency run, use concurrency 1 per provider and randomize which provider is called first for each packet. Record scheduling order and timestamps to expose time-of-day effects.
- Measure end-to-end latency with a monotonic clock from adapter submission to terminal result, including retry delays. Also record attempt-level provider latency where available.
- Honor provider rate-limit instructions. Use one controlled retry policy with at most three total attempts, including SDK retries; avoid nested retry multiplication. Record any provider-specific differences.
- Enforce the approved spending cap before each batch. If a call cannot be budgeted conservatively, stop and prepare the amended budget for review.
- Persist raw requests/responses and usage without API keys. Use a resume key derived from experiment, study track, repetition ID, model, configuration, rubric, and packet hashes. Cached saved outputs are valid for resuming analysis but must not be counted as new judgments or live latency observations.
- Record whether provider prompt caching applied. Separate actual billed cost from a documented uncached estimate; do not equate token counts across tokenizers.
- Detect returned-model changes where the provider exposes them. Stop on a material model/configuration change and request a reviewed protocol amendment.
- Reconcile all packet × judge pairs to terminal outcomes before analysis. Do not rerun only unfavorable valid outputs. Transport-error reruns must follow the frozen retry policy.

### Bounded repeatability run

1. Complete the designated primary calls before extra repetitions, so repeated-prompt cache warming cannot change the primary measurements. Keep valid primary results fixed.
2. Load only packets in the G3-approved repeatability manifest. The default is 20 packets × 2 judges × 10 total observations, including their primary calls. This adds **360 logical evaluations**: 20 × 2 × 9. If a logical evaluation uses multiple API requests, budget all of them and their permitted retries.
3. Issue fresh calls for repetition IDs 1–9 under identical evidence, prompts, model configuration, and criterion ordering. Randomize or interleave packet/provider order with the frozen scheduling seed. Disable application-level response caching for new repetition IDs; record provider-side prompt caching when observable.
4. Do not rerun the support agent, refresh retrieval evidence, perturb prompts, or change seeds/settings between repeats. Variation should come from repeated service responses to the same configured request. A separate robustness study can vary inputs later.
5. Account for every planned repetition, including errors and budget-blocked work. Do not replace an unfavorable valid judgment. A missing primary result stays missing in primary analysis even if later repeats succeed.
6. Keep the repeatability ledger and timing/cost summary separate. Its primary observation is linked to the original ledger record, not double-billed. Repeated requests may have different cache behavior from one-off evaluations.

If the run must change after test outputs or labels are inspected, record the deviation and treat the affected analysis as exploratory. A fresh confirmatory test needs previously untouched task groups or a new dataset.

## 10. M6 — Metrics, uncertainty, and conclusions

### Accuracy and reliability

Let a human-labeled failure be the positive class. On criterion-specific human PASS/FAIL cases with a valid judge decision, report:

```text
missed-failure rate (FNR) = human failures judged PASS / human failures
false-alarm rate (FPR)    = human passes judged FAIL / human passes
failure recall           = 1 - FNR
failure precision        = true detected failures / all predicted failures
decision coverage        = valid decisions / human-scorable cases
```

Show the exact denominator for every metric. In valid-decision confusion matrices, the first two denominators include only valid decisions in the respective human class. Also report paired complete-case comparisons, per-judge valid-case metrics, and an all-cases accounting table, so missing outputs cannot improve a judge's apparent performance unnoticed.

For an operational view, route missing/invalid decisions to review: report the share of **all** human failures automatically passed, the share of human passes unnecessarily flagged, and the total review/defer rate. Do not count a failed API call as a correct rejection. Report applicability/unscorable rates separately from API decision coverage. Do not compare accuracy without the corresponding coverage.

Per criterion and judge, include confusion counts, FNR/FPR, precision/recall, coverage, and class prevalence at both frozen thresholds. Primary conclusions concern grounding; secondary criteria are descriptive unless a multiple-comparison plan was approved.

### Probabilities, cost, and speed

- Evaluate raw `p_failure` against human failure labels using Brier score and log loss, with a documented numerical clip for log loss. Include a reliability plot when sample size supports it. Do not interpret a high confidence value as evidence of correctness.
- Report mean cost per attempted **complete response evaluation**, cost per valid result, projected cost per 1,000 complete evaluations, and total actual experiment spend. Include all criterion calls, retries, and charged reasoning/output tokens. Report per-API-call metrics only as a secondary breakdown; they are not the denominator for the replacement decision. Separate primary inference, repetition, upstream replication if any, tuning, annotation, and optional platform costs.
- Report p50 and p95 end-to-end latency and sample counts, separately for successful calls and all terminal attempts. Do not describe p99 from a small sample as stable.
- Compare cost/latency at the frozen operational thresholds alongside their observed test FNR, FPR, and coverage. If quality is not comparable, describe the tradeoff rather than claiming equivalent quality.

### Repeatability analysis — separate secondary results

Analyze repeated judgments within each selected packet and criterion using raw, uncalibrated probabilities and the frozen fixed threshold. Report:

- Planned and valid repetition counts, missing/error rates, and both-judge coverage for every packet.
- Within-packet sample standard deviation, variance, and range of `p_failure`, where at least two valid observations exist. Do not pool variation across different packets as model instability.
- Binary disagreement: the fraction of valid verdicts differing from that packet's modal verdict; use the smaller PASS/FAIL count divided by valid observations. Report the fraction of packets unanimous across all planned observations, and distinguish this from unanimity among only available responses.
- The number of packets that are consistently correct, consistently wrong, or mixed relative to human labels. Require every planned observation to be valid before calling a packet consistently correct or wrong; report incomplete packets separately. Being consistently wrong is not a reliability success.
- Full-evaluation cost and latency for the additional repetitions, separately from primary calls, including observed caching. Do not mix this sample into the primary throughput or cost estimate.

Aggregate these measures by packet and show length strata. Treat stratified-subset results as descriptive unless approved weighting supports a corpus-level estimate. Resample whole task families for paired uncertainty, retaining all repeats and both judges. Report absolute variation as well as ratios; a zero Jev variance does not justify an infinite improvement claim.

The default 20 packets × 10 observations offers a bounded repeatability check, not a precise estimate for all support scenarios. Extra repeats do not increase the primary accuracy sample, become majority-vote replacements for primary judgments, or prove an architectural explanation for any stability difference.

### Statistical analysis

For primary metrics, filter to `study_track=primary` and `repetition_id=0` before analysis. Resample complete `task_family_id` groups with replacement, retaining all source-agent trajectories and both judges' designated primary outputs together. Use the configured 10,000 paired cluster-bootstrap replicates to estimate uncertainty for metric differences. Freeze whether the headline estimand weights trajectories or task groups; default to trajectory-weighted metrics with a task-weighted sensitivity analysis. Analyze judge repetitions only under the separate repeatability procedure above.

If resamples lack a required class, report their frequency and the resulting limitation; do not silently discard them to produce a narrow interval. Rare failures, few independent groups, or insufficient paired valid decisions can make a noninferiority conclusion unavailable. A degenerate bootstrap, including zero observed errors or identical paired outcomes, cannot justify zero uncertainty about unseen cases. Use a sparse-event method specified and reviewed before test access, or mark the formal claim inconclusive; do not select a favorable method after inspecting the results.

For the primary preregistered noninferiority claim, define `delta_FNR = FNR_Jev - FNR_frontier` and the analogous FPR difference on paired-valid grounding cases at threshold 0.5. Use one-sided 95% upper confidence bounds against the G0-approved margins; separately enforce the approved minimum coverage and upper bound on the all-cases missed-failure difference. Report ordinary two-sided 95% intervals for descriptive comparisons. Both error-rate requirements and the safeguards must pass. Do not claim equivalence merely because a difference is not statistically significant.

Support the practical claim only if the approved quality, coverage, cost, and latency criteria all pass. Otherwise report a narrower supported conclusion or an inconclusive result. The report must state that this is an offline study of one benchmark/domain/source-agent configuration, not proof of production performance or of superiority across agents.

### Report contents

Include the protocol/version summary, upstream audit and intentional changes, corpus flow counts, criterion-level human failure counts, annotation agreement, primary paired results with intervals, repeatability results, other secondary metrics, complete-evaluation cost/latency, and a balanced error analysis. Clearly label upstream published numbers versus our new measurements. Review at least 10 judge disagreements when available, or all if fewer; add shared errors and representative successes. Cite evidence IDs and human labels. Do not use these post-test examples to retune the reported experiment.

Record public-benchmark contamination as an unresolved limitation: the judges' training exposure to tasks or transcripts may be unknown. Using a benchmarked agent makes the source reproducible; it does not eliminate questions about benchmark realism or generalization.

**G4 package:** final report, machine-readable metrics, reproducibility instructions, deviations, limitations, and an explicit checklist mapping each proposed claim to its evidence. Prepare the report before requesting publication approval; do not publish or message third parties automatically.

## 11. Optional follow-up experiments

Only after the primary comparison is complete and reviewed:

- **Second source agent:** repeat on another model family. Retain task-family splits across sources; new trajectories of a task already used for tuning are not an independent unseen-task confirmation.
- **Another domain:** evaluate airline or another supported environment using a separately reviewed policy rubric and untouched task groups.
- **Controlled response defects:** create clearly labeled perturbations such as wrong amounts or false completion claims. Keep this diagnostic dataset separate from natural response metrics and prevalence estimates.
- **Jev-to-frontier routing:** use development data to choose when Jev should defer to the frontier model. Evaluate the combined system against human labels and include both calls' cost and sequential latency. Compare it with appropriate single-judge deferral baselines.
- **Live evaluation or intervention:** study later under a separate design. An evaluator that changes the support agent's behavior is a different experiment from offline judging.

## 12. Project deliverables and command contract

Implement a small Python project with a pinned environment, adapting the upstream components identified in Section 0. Prefer a CLI and machine-readable artifacts over notebooks that require manual state. The archive audit and saved-data report must not require model credentials or initialize clients that make model calls.

| Path | Required contents |
| --- | --- |
| `README.md` | This protocol plus verified setup/reproduction instructions |
| `pyproject.toml` and dependency lockfile | Reproducible runtime and development dependencies |
| `configs/experiment.yaml` | Approved experiment configuration |
| `protocol/` | Frozen rubric, prompts, analysis plan, protocol versions, and upstream reuse/change record |
| `src/judge_compare/` | Upstream archive audit, ingest, normalize, split, adapted evaluators, primary/repeatability execution, metrics, and reporting |
| `tests/` | Targeted contract, leakage, accounting, and metric checks |
| `data/raw/` | Immutable source files, subject to source license |
| `data/upstream_archive/` | Pinned weather-study artifacts and provenance, kept separate from support data |
| `data/packets/` | Canonical evidence packets with no labels |
| `data/manifests/` | Provenance, hashes, splits, exclusions, repeatability subset/schedule, and run manifests |
| `annotations/` | Human annotations, adjudications, and development labels; sealed test labels controlled separately until release |
| `runs/` | Raw requests/responses, normalized decisions, and append-only ledger with study/repetition/attempt IDs; optional LangSmith correlation IDs |
| `reviews/` | Human approvals, requested changes, and artifact hashes |
| `reports/` | Upstream audit, final report, separate primary/repeatability metrics, cost breakdowns, and figures |

Implement these commands or documented equivalents, defaulting to `configs/experiment.yaml`. Commands must fail clearly when prerequisites are missing; both `run` and `repeat` must validate approvals and remaining budgets. `audit-upstream` may download source/archive files but must never invoke the weather agent or either judge.

```bash
python -m judge_compare audit-upstream --no-model-calls
python -m judge_compare ingest --config configs/experiment.yaml
python -m judge_compare prepare --config configs/experiment.yaml
python -m judge_compare validate --stage corpus
python -m judge_compare annotations export --split dev --pilot
python -m judge_compare annotations import --file PATH
python -m judge_compare validate --stage gold-and-adapters
python -m judge_compare run --split dev --judges jev,frontier
python -m judge_compare freeze --config configs/experiment.yaml
python -m judge_compare run --split test --study-track primary --judges jev,frontier --resume
python -m judge_compare repeat --manifest data/manifests/repeatability_manifest.json --resume
python -m judge_compare report --manifest PATH
```

Annotation export/import must also support the full development and test sets after G1. Keep annotation storage separate from request construction. `prepare` creates the initial repeatability manifest from the frozen split without labels; `freeze` records any G3-approved size/budget adjustment. `report` must run from saved data without further model calls or LangSmith access. Record unsupported upstream reconstruction explicitly instead of presenting an archived summary as an independently reproduced calculation.

## 13. Human review procedure

For G0–G4, write `reviews/Gx-request.md` containing the decision needed, artifact links and hashes, acceptance-check results, unresolved issues, and the next authorized action. Present it to the experiment owner in the active conversation; do not send email, Slack, or external messages.

Record the human's actual decision in `reviews/Gx-decision.yaml`:

```yaml
gate: G3
decision: pending  # approved | revise | stop, as explicitly decided by the human
reviewer: null
reviewed_at: null
artifact_hashes: {}
approved_scope: null
approved_budget_usd: null
conditions: []
```

The implementation agent cannot approve its own gate or infer approval from silence. While a gate is pending, it may complete reversible preparation and local validation that do not cross the gate. Once the human approves the concrete scope and budget, proceed within them without asking again for each routine batch.

A material change to corpus, rubric, model, budget, or analysis after its approval requires an amendment specifying which downstream artifacts are invalidated. Formatting fixes do not require a new scientific review. If the test has already been inspected, disclose that fact in the amendment and conclusions.

## 14. Definition of done

- [ ] The pinned upstream experiment is audited, credited, and distinguished from our new results; reused and changed components are documented.
- [ ] Source agent, benchmark revision, response corpus, and judge models are identifiable and frozen.
- [ ] All milestone acceptance checks pass or have a reviewed, explicit limitation.
- [ ] Human rubric/label review is complete; competing judges did not create the answer key.
- [ ] Both judges received the same packets and criterion definitions.
- [ ] The holdout was evaluated under the G3-approved configuration.
- [ ] The bounded repeatability study has explicit planned/actual counts, fresh-call accounting, stable-error analysis, and its own cost/latency results.
- [ ] Extra repetitions do not enter primary accuracy denominators or primary inference cost/latency estimates.
- [ ] Every source example and API request is accounted for.
- [ ] Accuracy, uncertainty, coverage, calibration, cost, and latency are reported.
- [ ] A second person can regenerate our new report numbers offline from saved artifacts; any quoted upstream results or unavailable source data are clearly identified.
- [ ] G4 approves the final conclusions; unsupported claims are removed.

The deliverable is a reproducible measurement of the tradeoffs. A claim that Jev is a practical replacement follows only if the measured evidence supports the predefined requirements.
