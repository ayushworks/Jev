# Jev vs. a frontier model as customer-support response judges

## Goal

Evaluating AI agents today means choosing between deterministic checks
(rigid but fast and cheap) and LLM judges (flexible but slow and expensive).
This experiment asks whether Jev — a specialized decision model that returns
structured verdicts instead of free-text critiques — can close that gap,
providing evaluation signal with the agreement, stability, and cost profile
needed for frequent testing.

Jev is compared head-to-head against GPT-5.4 (medium reasoning effort) as a
judge of customer-support agent trajectories, with both models receiving
identical structured state and question strings. Each judge scores five
dimensions of a support interaction — task success, policy compliance, tool
correctness, response groundedness, and customer communication clarity —
using Jev's `Noul` primitive (yes/no propositions returning probabilities).
A single human reviewer's labels form the reference against which both
judges are measured, so results reflect agreement with that reviewer, not an
independent ground truth.

Background and results: [Jev as a Judge: Can a decision model score customer support agents as well as a frontier LLM?](https://ayushm4489.medium.com/jev-as-a-judge-can-a-decision-model-score-customer-support-agents-as-well-as-a-frontier-llm-4af637a9bd15)

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

Commands are introduced milestone by milestone and fail closed when their
prerequisites, approvals, or budgets are missing; run `--help` on the CLI for
the current command set.

## Layout

- `configs/` — reviewed experiment configuration
- `src/judge_compare/` — audit, ingest, validation, execution, and analysis code
- `data/` — immutable source material, canonical packets, and manifests
- `annotations/` — immutable human reference-label records
- `runs/` — append-only requests, responses, and cost ledger
- `reports/` — audit and experiment reports
