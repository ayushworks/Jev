# G0-approved $30 judging budget

> **Status:** API ceiling approved at G0 on 2026-09-21. No model or
> token-counting API calls were made to prepare it. Paid development calls still
> require G2 and paid holdout calls require G3.

## Scope covered by the cap

The candidate source contains 456 structurally eligible retail-support
trajectories. The revised proposal samples at most **21 customer-support
cases** and judges the final customer-facing response in each case once with
Jev and once with GPT-5.4. It does not rerun the support agent, run the archived
weather agent, add another comparator, or perform a repeatability study.

Each logical judgment sends all three criteria in one batched request. The
21-case proposal therefore permits 42 logical evaluations and at most 126 paid
attempts: 21 cases × 2 judges × 3 attempts. SDK retries remain disabled so the
common harness owns that limit.

The **$30 ceiling covers every billable judge API attempt**. Human annotation
labor is necessary but is not an API charge and must be budgeted separately.

## Price snapshot

Prices checked 2026-09-21:

| Judge/model | Uncached input / 1M | Cached input / 1M | Output / 1M |
| --- | ---: | ---: | ---: |
| Jev `jev-1.13.0` | $0.042 | not separately priced | free |
| OpenAI `gpt-5.4-2026-03-05` | $2.50 | $0.25 | $15.00 |

GPT-5.4 reasoning tokens are billed as output tokens and count toward
`max_output_tokens`. The proposal uses Standard synchronous processing with no
regional-processing endpoint; a regional endpoint would carry a 10% GPT-5.4
uplift and is outside this envelope. No cache discount is assumed.

Primary sources: [TypeSafe models](https://docs.typesafe.ai/models),
[GPT-5.4 model and pricing](https://developers.openai.com/api/docs/models/gpt-5.4),
and [OpenAI reasoning-token accounting](https://developers.openai.com/api/docs/guides/reasoning).

## Guaranteed case count

The current request caps are 32,000 GPT-5.4 input tokens, 25,000 GPT-5.4
output tokens, and Jev's separate 64,000-token total-request limit. At the
uncached Standard rates, one fully consumed attempt costs:

```text
GPT-5.4 = 32,000 * $2.50/M + 25,000 * $15/M = $0.455000
Jev       = 64,000 * $0.042/M                    = $0.002688
paired attempt                                         = $0.457688
paired case, three attempts per judge                  = $1.373064
```

Therefore:

```text
floor($30 / $1.373064) = 21 cases
21 cases = $28.834344
22 cases = $30.207408  (not permitted)
unallocated hard-envelope margin = $1.165656
```

**Twenty-one is the maximum case count guaranteed to finish within $30 under
the reviewed caps and retry policy.** This deliberately pessimistic envelope
assumes every allowed attempt is charged and consumes every input and output
token. With retries disabled entirely, the analogous maximum would be 65
cases, but that is not the proposed reliability policy.

## Expected-cost sensitivity, not authorization

The archived source-model prompt count is only a packet-length proxy: median
9,529, mean 9,642, p95 13,044, maximum 14,631 tokens. Canonical packets will
add the frozen rubric, reconstructed tool definitions, serialization, and
provider-specific tokenization. GPT-5.4 reasoning/output usage is not known
until development calls are allowed.

The following one-attempt illustrations assume the same proxy input count for
both judges and no cache credit. GPT output includes hidden reasoning and
visible structured JSON.

| Input proxy | GPT output | Estimated paired cost/case | Nominal cases under $30 | Estimated cost for all 456 |
| ---: | ---: | ---: | ---: | ---: |
| mean 9,642 | 1,000 | $0.03951 | 759 | $18.02 |
| mean 9,642 | 2,000 | $0.05451 | 550 | $24.86 |
| p95 13,044 | 2,000 | $0.06316 | 475 | $28.80 |
| p95 13,044 | 2,500 | $0.07066 | 424 | $32.22 |
| p95 13,044 | 3,000 | $0.07816 | 383 | $35.64 |

These figures explain why a later amendment might support many more cases, but
they are not safe commitments: the proxy is not the serialized judge input,
reasoning usage can vary substantially, and retries can be billable. Running
all 456 would require average billed GPT output/reasoning below about 2,752
tokens at the mean input proxy, or about 2,175 tokens at the p95 proxy, with no
retries.

## Repeatability disposition

Repeatability is disabled. The former K=15 design adds 135 paired
case-equivalents. Its strict three-attempt envelope alone is $185.36, so it
cannot fit a $30 authorization ceiling. Unused money from shorter responses
does not silently authorize repetitions.

## Spend-control contract

For each attempt, the immutable local ledger must calculate actual cost from
provider-reported usage and the frozen price basis:

```text
attempt_cost =
    uncached_input_tokens * uncached_input_rate
  + cached_input_tokens   * cached_input_rate
  + output_tokens         * output_rate
```

Before any paid call, the runner must:

1. verify the exact model ID, price basis, Standard/non-regional endpoint, and
   remaining case/request allowances;
2. reject packets above either provider's approved input limits;
3. reserve the maximum cost of the next attempt before dispatch;
4. refuse the call and record `budget_blocked` if the running total plus the
   reservation could exceed $30; and
5. preserve the request, terminal status, provider usage, retry reason, and
   actual cost locally.

The five-case technical pilot, if approved, must be drawn from the 21-case
limit rather than added to it. Its development results can replace proxy
assumptions for a future, pre-holdout G0/G3 amendment. No automatic expansion
past 21 cases is allowed under this proposal.
