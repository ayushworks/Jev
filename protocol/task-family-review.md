# Retail task-family derivation review

> **G1 review status:** reviewed task-family mapping proposal, **pending G1
> approval**. This document does not freeze or finalize the M1 corpus or pools.
> The mapping below may be used to prepare the G1 corpus package, but it is not
> an approved scientific input until the G1 reviewer accepts it. Any change
> after selection requires regenerating the grouped split and both pools before
> labels or judge outputs are inspected.

## Bound source and permitted evidence

This review covers the 114 retail task definitions in the approved local source:

| Artifact | SHA-256 |
| --- | --- |
| `claude-sonnet-4-5_enabled_retail_gpt-5.2_4trials.json` | `f344a3a63783018b693f2a1a60b80b9d4f86fce6d5df74c0fcba647baacbea00` |
| Compatibility task definitions at `17e07b1da2bbc0cadfddeea36412686e0604127b` | `8e03ebce7901bd6218e7a7dc3105faa9324091a68058f7fe61c65262868812e8` |

Only task IDs and the visible user-scenario instruction fields were considered:
`reason_for_call`, `known_info`, `unknown_info`, and `task_instructions`.
Benchmark rewards, evaluation criteria, expected actions, trajectory outcomes,
human labels, and judge outputs were not used.

The derivation is an explicit reviewed map, not an inferred semantic-clustering
model. A cross-ID family requires evidence of the same underlying scenario:

1. a specific shared customer/account context;
2. material overlap in specific items, order identifiers, or dependent requests;
   and
3. near-duplicate, alternate-resolution, inverse-control, or composite-bridge
   structure.

Customer identity alone, a common product category, or a broad action such as
"return" or "exchange" is insufficient. Four trials of one task ID inherit that
task's family. Family IDs are deterministic and use the lowest task ID in the
family: `retail-task-family-NNN`.

## Frozen multi-ID mapping

| Task family ID | Task IDs | Scenario/entity rationale |
| --- | --- | --- |
| `retail-task-family-000` | 0, 1 | Same Yusuf Rossi order `#W2378156`, keyboard and thermostat exchanges; only the unavailable-keyboard fallback changes. |
| `retail-task-family-003` | 3, 4 | Same Yusuf Rossi T-shirt inventory query and pending purple small/V-neck/polyester modification; singular/plural and punctuation variation only. |
| `retail-task-family-005` | 5, 6, 7, 8, 9 | Same Mei Kovacs water-bottle and desk-lamp exchange, with brightness, power-source preference, and confirmation branches varied. |
| `retail-task-family-010` | 10, 11 | Same Mia Garcia two-order return and cross-payment-method request; the infeasibility fallback is escalation versus original-method refund. |
| `retail-task-family-012` | 12, 13, 14 | Same Mia Garcia gaming-item selection scenario using the keyboard and mouse; payment fallback varies, and task 14 is the explicit inverse-control variant. |
| `retail-task-family-023` | 23, 70 | Same Sofia Hernandez helmet exchange to medium/high ventilation, with red versus blue and composite versus helmet-only scope. |
| `retail-task-family-025` | 25, 26 | Same Isabella Johansson Texas shipment, tracking request, and return-everything-except-pet-bed scenario; task 25 adds refund and handoff contingencies. |
| `retail-task-family-027` | 27, 28, 29 | Same Isabella Johansson multi-order item scenario. Tasks 27/28 share hose and backpack; tasks 28/29 share skateboard and garden hose, so task 28 forms a specific entity bridge. |
| `retail-task-family-030` | 30, 31, 32 | Same Olivia Lopez tablet incident, tracking request, dependent charger action, and sneaker return; damage versus loss and the boot/kettle branch vary. |
| `retail-task-family-033` | 33, 34 | Same Noah Patel work-from-home office-versus-hiking cancellation, refund total, and address contingency; only the infeasibility fallback changes. |
| `retail-task-family-036` | 36, 37, 38 | Same Daiki Sanchez credit-limit cascade: split payment, remove the most expensive item, downgrade, then cancel; dollar thresholds and confirmation wording vary. |
| `retail-task-family-041` | 41, 42 | Same Mei Patel address verification/correction and easiest-jigsaw modification; request order and default-payment detail vary. |
| `retail-task-family-045` | 45, 46, 47, 48 | Same Daiki Johnson order context and robotic/canister vacuum plus air-purifier entities; exchange, return, subtype, and eligibility branches are alternate resolutions. |
| `retail-task-family-051` | 51, 52 | Same Sofia Li received digital camera; return versus maximum-zoom exchange are alternate resolutions of dissatisfaction with that specific item. |
| `retail-task-family-054` | 54, 55 | Same Amelia Silva financial-distress request to cancel or return all possible purchases; task 54 adds a cheaper-boots exception. |
| `retail-task-family-056` | 56, 57 | Same Ivan Hernandez pending air-purifier order and gift-card contingency; partial cancel/downgrade versus whole-order cancel varies. |
| `retail-task-family-060` | 60, 61 | Same Chen Johnson order `W5061109` blue-earbuds modification at equal-or-lower price; optional water-resistance preference is omitted in task 61. |
| `retail-task-family-062` | 62, 63 | Same Chen Johnson poem redirection followed by Bluetooth-speaker price, cancellation, replacement, and total-price flow; price threshold and conditionality vary. |
| `retail-task-family-067` | 67, 68 | Same Noah Ito most-recent-order total query; only the nickname/persona wording differs. |
| `retail-task-family-071` | 71, 72 | Same Ivan Khan DC-to-Charlotte address correction, desk-lamp/backpack changes, and gift-card-to-PayPal reversal; phrasing and request order vary. |
| `retail-task-family-082` | 82, 83, 84 | Same Chen Silva two-tablet return and refund-method scenario; selected tablet and refund fallback/reversal vary. |
| `retail-task-family-085` | 85, 86, 87 | Same Yusuf Hernandez fleece-jacket and Washington-DC-address scenario; task 86 is the composite bridge between jacket-only task 85 and address-only task 87. |
| `retail-task-family-091` | 91, 92 | Same Mei Ahmed skateboard returns plus smartwatch/e-reader request; task 91 adds an e-reader exchange fallback. |
| `retail-task-family-093` | 93, 94, 95 | Same Lei Wilson laptop exchange to i7/8GB/1TB; source-laptop memory, count, and payment-total request vary. |
| `retail-task-family-096` | 96, 97 | Same Yusuf Li LA-to-NYC order-address change and cheapest green speaker exchange; task 97 adds a request-order instruction. |
| `retail-task-family-098` | 98, 99 | Same Sofia Li bicycle, jigsaw, camera, and skateboard composite request; theme, payment method, and cancellation contingency vary. |
| `retail-task-family-101` | 101, 102 | Same Noah Ito watches/address/material changes and air-purifier exchange; the companion order contents differ. |
| `retail-task-family-103` | 103, 104 | Same Lucas Brown bookshelf/jigsaw/backpack returns, pending-order address/color change, and tracking request; order multiplicity differs. |
| `retail-task-family-109` | 109, 110 | Same Sophia Martin move-related order/default-address correction and cheapest-tablet exchange; the item identifying the new-address order differs. |
| `retail-task-family-111` | 111, 112 | Same Yara Silva laptop NYC-address/configuration change and black-dial watch exchange; laptop attributes versus item ID and conversational ordering vary. |

## Frozen singleton mapping

The following task IDs each map to their own `retail-task-family-NNN` family:

```text
2, 15, 16, 17, 18, 19, 20, 21, 22, 24, 35, 39, 40, 43, 44, 49,
50, 53, 58, 59, 64, 65, 66, 69, 73, 74, 75, 76, 77, 78, 79, 80,
81, 88, 89, 90, 100, 105, 106, 107, 108, 113
```

These 42 tasks lack enough visible evidence of cross-ID scenario-variant
lineage. This remains true when another task shares a customer name, account,
product type, or action.

## Critical review changes and edge cases

The first implementation proposed 69 families. Critical review increased this
to **72 families** by rejecting links that were not supported strongly enough:

| Prior proposal | Frozen disposition | Reason |
| --- | --- | --- |
| 2 with 3/4 | Task 2 is singleton; 3/4 remain grouped | The T-shirt inventory sub-request is shared, but task 2's return request is not a variant of the pending-shirt modification. |
| 18 with 19 | Both singleton | Same Mei Davis account and office-chair entity are insufficient; the item sets and requested resolutions are materially different. |
| 20 with 21 | Both singleton | Same Ethan Garcia account, gift card, and exchange theme are insufficient; one is an all-item upgrade and the other is a staged exact-item request. |

The following non-obvious groups remain intentionally conservative:

- **23/70:** exact account plus the same medium, high-ventilation helmet request;
  color and composite scope are the variation.
- **27/28/29:** the grouping is a transitive entity-overlap graph, not a broad
  "returns" cluster; task 28 links the hose/backpack request to the
  skateboard/garden-hose request.
- **85/86/87:** task 86 explicitly combines the jacket request from 85 and the
  DC-address request from 87, making it a composite bridge.
- **45--48 and 51/52:** these are alternate resolutions involving the same
  specific account/order items. G1 should explicitly accept or split these two
  judgments rather than treating them as mechanically established.

## Split and pool feasibility consequence

With 72 families, the frozen split rule (`development_fraction: 0.25`, seed
`20260920`) assigns 18 families to development and 54 to test. The label-blind
selection rule can draw five model-study cases from distinct development
families, 16 from distinct test families, and 24 calibration-only cases across
12 other development families. One development family remains as capacity
headroom before packet-eligibility exclusions in the original frame.

The owner-visible timing/training example reserves
`retail-task-family-096`—tasks 96 and 97, all eight trajectories—from both
measured pools. This family belongs to the original development assignment, so
17 eligible development families remain: exactly five for the model study and
12 for calibration. No development-family headroom remains. Preserve the
documented 18/54 assignment and record the reserved family as an exclusion; do
not silently rerun the split over 71 families. Any further development-family
or context exclusion requires an amendment.

This exposure-driven exclusion is outcome-blind but narrows the measured
sampling frame by 8 of 456 packets and 1 of 72 families. Reports must identify
the omitted Los-Angeles-to-New-York address plus green-speaker scenario family
and must not generalize the measured result to family 096.

The separate source-versus-normalized packet audit must not expose a family
that Ayush will later annotate in the 45-case measured set. Its private
reviewed-ID inventory must therefore use one packet from each of at least 10
distinct, otherwise-unselected **test** families, with zero family overlap with
the model-judgment pool, calibration pool, or timing-exposed family 096. This
audit sample does not alter either provisional pool.

This is a feasibility result using the audited source prompt-length proxy. The
final pool manifest must be generated from canonical eligible packets and
their serialized lengths. If exclusions leave fewer than 12 otherwise-unused
development families or cannot supply 24 calibration cases, stop for an
amendment; do not borrow test families or inspect labels to repair the sample.

## Required G1 decision

G1 must record one of:

1. approve this 72-family map unchanged;
2. request specified family edits and regenerate every downstream split/pool
   artifact before annotation or judge access; or
3. reject the source because defensible grouping cannot be established.

Approval must confirm that grouping evidence is limited to the permitted task
definition fields and that the edge-case judgments above are acceptable.
