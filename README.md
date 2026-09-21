# Jev vs. a frontier model as customer-support response judges

## Goal

This repository compares Jev with one reviewed frontier-model configuration
as judges of customer-support responses, on identical, frozen evidence
packets. A single human reviewer's labels — not either model's — form this
run's reference labels, so results measure agreement with that reviewer,
not objective human accuracy.

The project is currently in **M1 (corpus and evidence-packet construction)**.
No judge calls have been made and no experiment result is claimed.

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
