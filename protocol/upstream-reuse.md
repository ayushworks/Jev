# Upstream reuse and change record

Upstream reference: `https://github.com/danielgshea/jev-as-a-judge` at
`adfea74905f721ea2594e22804c8c8edf1693163`.

The audited pin does not contain a license file or package-license declaration.
Accordingly, the initial implementation copies no upstream source code. It uses
the public implementation only as a scientific and interface reference. This
decision must be revisited if explicit reuse permission or licensing terms are
obtained.

| Upstream area | Initial treatment | Planned support-study change |
| --- | --- | --- |
| Shared evidence builder | Conceptual reference | Canonical allowlisted packet containing policy, tools, conversation prefix, and target response |
| Jev questions | Conceptual reference | Atomic criterion-level PASS propositions with raw `p_pass` |
| LLM typed output | Conceptual reference | Provider-native structured output with the same criterion definitions and `p_pass` fields |
| Frozen-input runner | Conceptual reference | Durable local ledgers, grouped splits, explicit study/repetition/attempt IDs, approval and budget guards |
| Archived analysis | Preserve and attribute | Separate human failure detection, missing-result accounting, complete-evaluation cost/latency, and bounded repeatability |
| LangSmith | Optional interoperability only | Local artifacts remain sufficient for resume and offline reporting |

Every later copied or adapted code fragment must be recorded here with its
source path, applicable license or permission, and local destination.
