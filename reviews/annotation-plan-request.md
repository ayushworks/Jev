# Human annotation plan — owner decision requested

**Status:** pending. No annotator has been engaged and no labor spending is
authorized.

**Requested reviewer:** Ayush.

## Recommended approval

Approve the [human annotation plan](../protocol/human-annotation-plan.md) with:

- two independent annotators, one senior adjudicator, and one data-custody role;
- a 24-case calibration-only pool and a disjoint study pool targeting 21 cases
  and never exceeding 21;
- zero judge calls on calibration cases;
- up to 90 base packet-annotator assignments across up to 45 unique support
  cases;
- an experienced-staff ceiling of **$3,200 cash and 64 combined hours**, using
  Ayush's recorded 30-minute estimate for one pass over all 45 conversations;
- custodian-controlled local/remote access with no download, copy-out, or public
  annotation platform; and
- G1 re-estimation from measured annotation times before any study labels are
  produced.

Reviewed plan SHA-256:
`9319874562dc88438040330ea4c2e00b89219f094ba4dd2e510854ded54e00ab`.

Supporting timing and exclusion artifacts:

- `reviews/annotation-timing-estimate.yaml`:
  `accdc0403706a858338605676c53eaa6913eb55ce2f558b2548f3485b66f2037`
- `data/manifests/annotation-timing-example.json`:
  `2f79c976dcb0e7274bbce95a5a9635df54fc122efed3bd2c37794cf7d48a2e7e`
- `reviews/annotation-timing-example.md`:
  `0168faf5d012a19448da74b8e13f25b937e76dfff5fb399854e7ba60494dba9a`

This labor allowance is separate from, and cannot increase, the approved
**$30 / 126-attempt judge-API cap**. A fresh calibration pool, recruiting or
platform fees, a third routine label, or model-generated labels would require a
separate amendment.

## Alternatives

The exact alternatives are **$2,250 / 64 hours** for internal/lean staffing and
**$4,750 / 64 hours** for specialist staffing. Selecting an alternative changes
only the labor ceiling and rates; it does not change double annotation,
adjudication, blinding, custody, or the disjoint-pool rule. Every listed dollar
and hour amount is a dual ceiling, not a spending target.

## Owner response

Record one of:

- **approve annotation plan at $3,200 / 64 hours** (recommended);
- **approve lean/internal at $2,250 / 64 hours**;
- **approve specialist at $4,750 / 64 hours**; or
- **revise**, with the requested staffing, rate, or workflow change.

Approval authorizes staff engagement plus training and calibration after M1;
study labeling remains gated on G1. It does not authorize OpenAI or TypeSafe
calls; paid development remains gated on G2 and holdout execution on G3.
