# G0-approved scope addendum

**Decision:** approved by Ayush on 2026-09-21. The exact reviewed artifacts and
scope are bound in [`reviews/G0-decision.yaml`](../reviews/G0-decision.yaml).

The governing `protocol-v2.md` remains preserved as reviewed. This addendum
records the approved budget-driven implementation choices where its larger
default design no longer applies.

## Binding changes

1. **Model-judgment sample:** at most 21 customer-support cases receive Jev and
   GPT-5.4 judgments. Across both judges and retries, no more than 126 paid
   attempts or $30 total judge-API spend is allowed.
2. **Calibration pool:** select 24 additional customer-support cases spanning at
   least 12 task families for human rubric calibration only. They receive no
   judge calls and are excluded from reported judge-comparison metrics.
3. **Separation:** calibration cases and task families must not overlap the
   21-case model-judgment sample.
4. **Repeatability:** disabled. Repeatability selection, manifests, schedules,
   calls, metrics, and budget-share checks are `not_applicable` for this run.
5. **Claims:** the 21-case comparison is descriptive and feasibility-oriented.
   It cannot establish the protocol's formal noninferiority or practical-
   replacement claim.
6. **Weather work:** the archived weather cases remain audit-only. No weather
   agent or weather judgments are executed.
7. **Execution gates:** G0 activates the resolved configuration but authorizes
   no paid call. Paid development calls require G2 approval; paid holdout calls
   require G3 approval.

## Interpretation of existing milestone language

- References to a 24-packet “development pilot” for human rubric calibration
  mean the separate annotation-only calibration pool above, not the paid
  model-development split.
- References to repeatability deliverables are satisfied by recording
  `not_applicable` and the G0 decision hash; no empty or synthetic
  repeatability study is created.
- M1 must derive and freeze families and the full grouped assignment, select the
  21-case model-judgment sample from the complete eligible frame, then draw the
  calibration pool from otherwise unselected development families. Both
  selections remain blind to labels, rewards, and judge outputs. If the
  approved 24 cases across at least 12 development families are unavailable,
  stop for an amendment rather than using test families.
- The human staffing and labor ceiling are deliberately outside the API budget
  and remain pending in
  [`reviews/annotation-plan-request.md`](../reviews/annotation-plan-request.md).
