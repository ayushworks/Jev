# G0-approved support-source selection

> **Status:** source and limitations approved at G0 on 2026-09-21. M1 must
> still derive task families, reconstruct tool schemas, validate packets, and
> freeze the sampled corpus before G1. This investigation made no support-agent
> or judge calls.

## Leading candidate

| Field | Audited value |
| --- | --- |
| Submission | `claude-sonnet-4-5_sierra_2026-02-26` |
| Submission type | `standard` |
| Submission-declared tau2-bench version | `0.2.1-dev` |
| Domain | `retail` |
| Response-generating model | `anthropic/claude-sonnet-4-5-20250929`, temperature 0 |
| User simulator | `gpt-5.2`, low reasoning |
| Base seed / trial seeds | 300 / `626729`, `373753`, `361454`, `1567` |
| Run limits / mode | 200 steps, 10 errors, half duplex |
| Prompt verification | `modified_prompts: false`; `omitted_questions: false` |
| Tasks / trials / trajectories | 114 / 4 / 456 |
| Termination | `user_stop` for all 456 |
| Validated public compatibility pin | `17e07b1da2bbc0cadfddeea36412686e0604127b` |
| Raw trajectory SHA-256 | `f344a3a63783018b693f2a1a60b80b9d4f86fce6d5df74c0fcba647baacbea00` |

This run was selected because it is a published standard run, avoids paying to
generate new responses, uses a response model from a different family than the
approved OpenAI judge, and contains complete conversations, policy text, tool
calls/results, reward/termination metadata, and a deterministic final response
for every trajectory. The approved budget requires a label-blind sample from
the eligible trajectories.

The submission note claims extended thinking was enabled for Claude Sonnet 4.5,
but the trajectory's `agent_info.llm_args` records only `temperature: 0.0` and
no thinking budget. The exact source-agent reasoning configuration is therefore
not reconstructible; the raw response model ID is preserved, but this unknown
must remain in the provenance record.

## Structural eligibility and target extraction

All 456 trajectories have a nonempty last assistant message containing text;
that target message contains no tool call. Every target is followed by exactly
one simulator/user terminal message. The deterministic extraction rule is:

1. Select the last textual assistant response before the terminal simulator
   control message.
2. Preserve all earlier visible user/assistant messages and paired tool calls
   and results in order.
3. Exclude the later terminal simulator message as future evidence.
4. Strip only reviewed hidden reasoning and control metadata under one
   field-allowlist rule.

The 456 terminal controls are 359 `STOP`, 96 `TRANSFER`, and one
`OUT-OF-SCOPE`. They are termination metadata, not part of the evidence visible
when the target response was produced.

The raw artifact has 3,220 tool calls and 3,220 matched results, with no
unmatched pairs. The embedded retail policy is present in every simulation.
The artifact's `environment_info.tool_defs` is null, however, so usable tool
descriptions must be reconstructed from a reviewed compatible tau2-bench source
pin and checked against every observed tool name and argument shape before G1.

## Validated compatibility and remaining provenance gap

The regenerated audit completed with status `validated` and no validation
failures. The config, manifest, and audit now consistently use public
compatibility pin `17e07b1da2bbc0cadfddeea36412686e0604127b`. Against that pin:

- the embedded retail policy is byte-identical, and every simulation's policy
  matches it;
- the simulator guidelines are byte-identical;
- task ID sets match and all **114 of 114** normalized, release-shaped tasks
  match;
- all 16 observed tools exist at the pin, with no unknown tool or observed
  argument names. Required arguments, types, and full generated schemas remain
  an M1 reconstruction and validation task.

The task comparison explicitly excludes fields added only by the archived
artifact's serialization rather than the public release-shaped task schema:

```text
task.evaluation_criteria.actions[*].compare_args   # indices 0 through 12
task.evaluation_criteria.actions[*].requestor      # indices 0 through 12
task.evaluation_criteria.env_assertions
task.issues
task.issues[0].description
task.issues[0].pr_link
task.issues[0].resolution
task.issues[0].resolved_at
task.required_documents
task.ticket
task.user_tools
```

These exclusions define the compatibility comparison; they are not evidence
that the excluded artifact-only metadata exists in the public release shape,
and they must not leak evaluator criteria, expected actions, or hidden metadata
into canonical packets.

One provenance limitation remains. The trajectory advertises generation commit
`7324fb57cb6819e77937c79964db916f5b068e31`, which is not reachable in the
public tau2-bench repository. The submission declares tau2-bench `0.2.1-dev`,
and the selected public commit is a validated compatibility pin, not the
verified generation commit. Consequently G0 still cannot set
`source_run_revision_verified: true`; it must explicitly accept the compatible-
but-not-identical provenance basis.

The S3 trajectory URL is not content-addressed. The locally saved SHA-256,
retrieval timestamp, byte length, and source URL are therefore the immutable
provenance anchor.

## Grouping gap

The source has task IDs 0 through 113 and four trials per task, but no explicit
`task_family_id`. The 114 task IDs are not automatically 114 independent
families: related cross-ID variants may share an underlying scenario. Before
splitting, M1 must derive and review families from source task definitions or
other preregistered metadata, document the rule, and ensure zero family overlap
between development and test. Broad action labels such as “refund” are not by
themselves a family definition.

The 21-case model-judgment sample and separate calibration pool may be selected
only after this mapping, without labels, rewards, or judge outputs. Repeatability
is disabled for this run.

## Licensing and redistribution

The saved tau2-bench repository license is MIT, copyright Sierra Research, from
the compatibility source used by the audit. Its SHA-256 is
`e67c5aa0074dfcaefd3c3a1aedb94cb539234aecd15d5a972574e3200e6252fe`.
That supports reuse of repository materials subject to notice preservation.

The externally hosted S3 trajectory contains outputs from third-party models
and does not carry a separate license statement in the saved trajectory or
submission metadata. The repo MIT license's applicability to that external
trajectory and any redistribution rights are therefore not established by the
current artifacts. The proposed disposition is local research use only, with
the original file preserved unchanged and no public redistribution until the
owner confirms applicability or obtains permission.

Separately, the upstream `danielgshea/jev-as-a-judge` repository has no LICENSE
file or package license declaration at audited commit
`adfea74905f721ea2594e22804c8c8edf1693163`. Its implementation remains a
conceptual reference only; no source module may be copied until applicable
terms or permission are documented. See `protocol/upstream-reuse.md` and
`reports/upstream-audit.md`.

## Approved G0 disposition

Ayush accepted the following limitations and controls:

- the exact generation commit remains unverified;
- compatibility pin `17e07b1da2bbc0cadfddeea36412686e0604127b` is accepted as
  compatible rather than the unavailable exact generation revision;
- the validated 114/114 release-shaped task match and its explicit
  artifact-only field exclusions are preserved in provenance records;
- tool descriptions are reconstructed from the validated pin and their packet
  serialization is reviewed before G1;
- task families are derived and audited before the group split;
- the future terminal simulator message is excluded from every packet;
- both judges and humans receive the same canonical allowlisted packet;
- any packet exceeding either judge's limit is excluded from both;
- the raw trajectory remains local unless its redistribution terms are
  confirmed.

Failure of final tool-description or packet validation, inability to derive
defensible families, or a licensing decision that forbids the intended use
returns source selection to G0 rather than silently changing the scientific
task.

Supporting artifacts are `reports/candidate-source-audit.md`,
`data/manifests/candidate_source_manifest.json`, and the immutable files under
`data/raw/tau2/claude-sonnet-4-5_sierra_2026-02-26/`.
