# Benchmark-reward comparison amendment

Decision date: 2026-09-21  
Owner/reviewer: Ayush  
Conditions: none

## Decision

The owner directed the experiment to skip human annotation, including the separate
24-case annotation-only calibration pool, and finish the Jev-versus-GPT judge
comparison in one run.

The measured target is therefore the source τ² benchmark's binary task-success reward.
The resulting claim is limited to how well each judge predicts that benchmark reward;
it is not a claim about agreement with human judgments of general response quality.

## Frozen execution

- Evaluate the 21 already selected model-judgment cases.
- Give both judges identical task specifications, evaluation criteria, policy, tool
  definitions, conversation messages, tool calls, tool results, and target response.
- Withhold source rewards and evaluator outputs from both judges.
- Collect one valid probability per case per judge; repeatability testing is disabled.
- Classify as pass only when `p_pass > 0.5`.
- Permit at most three technical attempts per case/judge, with a global ceiling of 126
  attempts and a hard API budget of $30.
- Use GPT-5.4 snapshot `gpt-5.4-2026-03-05` at medium reasoning effort.
- Request `jev-latest` because it is the Jev alias exposed by the account, while recording
  the exact returned model on every call.

All case-level source rewards remain private execution data. The public report contains
aggregate and case-ID-level comparison results without exposing benchmark conversations.
