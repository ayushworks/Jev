# G0-approved judge configuration

> **Status:** approved at G0 on 2026-09-21. Paid development calls still require
> G2 and paid holdout calls require G3. Prices were checked on 2026-09-21 and
> must be checked again before execution.

## Approved choice

Use OpenAI `gpt-5.4-2026-03-05` at medium reasoning as the sole quality anchor.
It is from a different model family than the Claude Sonnet 4.5 response
generator, has an immutable dated identifier, and is substantially less
expensive than the previously proposed current-model anchor. It is not the
newest OpenAI model, so reports must call it a **quality anchor**, not imply that
it represents the current frontier.

| Role | Model | Reasoning | Standard token price per 1M | Snapshot qualification |
| --- | --- | --- | --- | --- |
| Proposed quality anchor | `gpt-5.4-2026-03-05` | `medium` | $2.50 input, $0.25 cached input, $15 output | Dated snapshot |
| Inexpensive candidate considered, not proposed as a paid comparator | `gpt-5.6-luna` | `medium` | $0.20 input, $0.02 cached input, $1.20 output | No dated suffix; positioned as the cost-sensitive/nano tier |
| Intermediate candidate considered, not proposed as a paid comparator | `gpt-5.6-terra` | `medium` | $2 input, $0.20 cached input, $12 output | No dated suffix |

The former Astra option and the Luna/Terra candidates are not part of the
revised paid scope. Adding any of them would require a separate call count and
budget amendment.

Primary sources: [GPT-5.4 model, dated snapshot, and pricing](https://developers.openai.com/api/docs/models/gpt-5.4),
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
and [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra).

## Proposed frontier request settings

The following settings apply to the proposed GPT-5.4 anchor unless G0 records
an explicit exception:

```yaml
provider: openai
api: responses
sdk_version: openai==3.16.2
reasoning_setting:
  effort: medium
sampling_settings:
  temperature: not_applicable
  top_p: not_applicable
  seed: not_applicable
max_input_tokens: 32000
max_output_tokens: 25000
timeout_seconds: 300
service_tier: default
store: false
tools: []
truncation: disabled
```

The response format should be a strict provider-native JSON schema containing
only one `p_pass` number in `[0, 1]` for each of the three criteria. Decisions,
`p_failure = 1 - p_pass`, and threshold comparisons belong in local code. A
refusal, incomplete response, missing field, non-finite number, or out-of-range
number is not silently repaired and must enter the terminal-status ledger.

Temperature, `top_p`, and token log-probability settings are omitted because
they are not supported controls for the proposed reasoning configuration. Tools
are disabled so the frontier judge sees only the frozen packet. The same rubric
semantics and evidence content go to both judges, although provider wrappers and
tokenization necessarily differ.

OpenAI warns that `max_output_tokens` includes hidden reasoning tokens and that
an undersized cap can produce an incomplete response before visible JSON. Its
starting recommendation is to reserve at least 25,000 tokens for reasoning and
output. The proposed 25,000-token cap therefore favors a credible quality
anchor over an artificially cheap comparator. A lower cap may be proposed at
G3 only under a G0-approved, label-blind technical rule based on development
usage and incomplete-response telemetry. See the official
[reasoning guide](https://developers.openai.com/api/docs/guides/reasoning) and
[structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs).

The proposed experiment-specific frontier input cap is 32,000 tokens, even
though the model supports more. Before either judge is called, a packet must
fit this frontier cap, Jev's 32k `state + longest question` limit, and Jev's
64k total-request limit under the applicable tokenizer/counting method. Failure
of any check excludes the packet from both judges; no truncation is allowed.

The proposed SDK pin is `openai==3.16.2`, released 2026-09-18. The harness
should construct the client with `max_retries=0`; the SDK otherwise retries
certain errors twice and defaults to a ten-minute timeout. See the official
[release list](https://github.com/openai/openai-python/releases) and
[retry/timeout documentation](https://github.com/openai/openai-python/blob/main/README.md?plain=1).

For every response, record the requested model, returned model, request ID,
usage including cached/cache-write/reasoning tokens where available, and the
price table version. A returned model mismatch or material provider-side model
change requires a stop and G0/G3 amendment; it is not a routine retry.

## Proposed Jev configuration

```yaml
provider: typesafe
model: jev-1.13.0
sdk_version: typesafe-sdk==0.7.0
primitive: noul
questions_per_request: 3
timeout_seconds: 300
```

Pin `jev-1.13.0`, not the moving `jev-latest` alias. Send the packet once as
shared state and batch three atomic, positively phrased Noul questions. Noul is
the probability that the answer is YES, so `p_pass = answer.noul`. Choice/Score
`confidence` is a separate distribution-concentration statistic and must never
be substituted for class probability. The exact criterion propositions are in
`protocol/rubric-draft.md`.

The official limits are 64k tokens for the complete request and 32k for `state`
plus the longest question. The packet builder must validate both and exclude a
packet from both judges if it cannot fit; no provider-specific truncation is
allowed. Published pricing is $0.042 per 1M input tokens with output free.
Published rate limits are 250,000 tokens/second and 1,200 requests/minute, but
TypeSafe states that these limits can change dynamically.

The proposed SDK pin is `typesafe-sdk==0.7.0`, released 2026-09-18. Instantiate
the client with `RetryPolicy(max_retries=0)` so the common harness, rather than
nested SDK retries, owns the three-attempt limit. The SDK's default timeout is
ten seconds, so the experiment must override it explicitly.

Primary sources: [TypeSafe model, context, alias, rate-limit, and pricing reference](https://docs.typesafe.ai/models),
[Noul semantics and batching](https://docs.typesafe.ai/primitives/noul),
[confidence versus probability](https://docs.typesafe.ai/confidence),
[Jev 1.13 documented limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13),
[Python SDK changelog](https://docs.typesafe.ai/sdk/python/changelog),
[client retry controls](https://docs.typesafe.ai/sdk/python/api/clients/async),
and [SDK constants](https://docs.typesafe.ai/sdk/python/api/constants).

## Shared execution proposal

- One batched request per logical evaluation and judge.
- Concurrency one per provider for the primary latency analysis.
- A true 300-second wall-clock limit per attempt, in addition to transport
  timeouts.
- At most three total attempts, with provider SDK retries disabled. Honor
  `Retry-After`; record delay in end-to-end latency.
- Retry only the approved transient classes. Invalid output remains visible in
  the attempt ledger and follows the frozen retry rule.
- Preserve every raw request and response locally. Never include credentials.
- No model initialization or call is permitted before the relevant gate and
  explicit budget approval.

## G0 decision recorded

Ayush approved the dated GPT-5.4 snapshot, SDK pins, medium reasoning, the
25,000-token cap, 300-second timeout, structured schema, retry ownership, $30
ceiling, 21-case limit, disabled repeatability, price snapshot, and model-change
stop condition. The active configuration is byte-identical to the approved
proposal. This decision does not bypass G2 or G3 execution gates.
