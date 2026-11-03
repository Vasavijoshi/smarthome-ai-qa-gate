# Investigation: faithfulness 0.0 with high correctness

Run `v1.0-20261103T152402` (golden only, full judging, commit `1cfba4a`). Read-only investigation, 2026-11-03.
No code, rubric, golden-set or stored-data changes were made.

## Question

Several golden cases scored faithfulness (M6) 0.0 while correctness (M8) was 0.7–1.0.
Is this a plumbing bug (empty or wrong context, truncated chunks, wrong field, failed claim extraction)
or a weakness of the judge model (`qwen2.5:7b`)?

## Cases examined

Only two cases meet faithfulness < 0.3 and correctness > 0.8 (G-TRB-005, G-TRB-008). The next three
closest (faithfulness 0.0, correctness 0.7–0.8) were added.

| Case | Reply (repeat 1) | Supporting sentence in the context passed to the judge? | Judge's stated reason | Verdict |
| --- | --- | --- | --- | --- |
| G-TRB-005 | "The filter life counter was not reset. Use the Filter Reset option…" | Yes, `jal_p2_purifier#troubleshooting/2` | Claims the reply contradicts the context | Judge error |
| G-TRB-008 | "Check that the fan is connected to a normal wall switch and not to a dimmer switch…" | Yes, `vayu_f1_fan#troubleshooting/1` | Reads "not to a dimmer switch" as recommending against the context | Judge error (negation) |
| G-NUM-008 | "Vayu F1 ke max timer 8 ghante ka hai." (Hinglish: 8 hours) | Yes, `vayu_f1_fan#timer` | Says the reply suggests a timer longer than 8 hours | Judge error (mixed-language reply) |
| G-TRB-007 | "…within 30 seconds hold the power button on the remote for 5 seconds until the fan beeps twice." | Yes, `vayu_f1_fan#remote_control` | Says "within 30 seconds" contradicts the context | Judge error |
| G-FAC-020 | "…comes with a limited one-year warranty, covering manufacturing defects and materials…" | No: retrieval returned the lock overview and two service-log chunks, not the warranty chunk | The warranty claim is not in the context | **Correct score**: the reply relies on facts that were not retrieved |

## Plumbing checks (all passed)

1. The stored `retrieved_json` is exactly what `judge_run` passes as DeepEval `retrieval_context`.
2. Every stored chunk text equals the v1.0 chunker's text for that chunk id (0 differences over the whole run),
   so nothing was truncated or swapped.
3. Re-running `FaithfulnessMetric` in memory on G-TRB-008 and G-TRB-005 reproduced 0.0 exactly.
   Its intermediate steps show:
   - **Truth extraction worked.** For G-TRB-005 the judge extracted, almost word for word, "If the purifier shows E4
     immediately after a filter change, the filter life counter was not reset…". For G-TRB-008 it extracted
     "A Vayu F1 ceiling fan may make a humming sound if connected to a dimmer switch."
   - **Claim extraction worked.** Each reply was reduced to one claim that matches its meaning.
   - **The verdict step failed.** The judge answered "no" (contradicted) for claims that the extracted truths support.

## Conclusion

The 0.0 scores come from **judge weakness at the faithfulness verdict step**, not from a plumbing bug.
Patterns seen: misreading negation ("not to a dimmer switch"), treating a claim that lost its "if" condition as a
contradiction, and misreading a Hinglish reply. One case (G-FAC-020) is a correct low score caused by a retrieval miss.

## Impact on the stored run (from SQLite, read-only)

- All 18 "unsupported claim" cases come from faithfulness < 0.7 alone; the forbidden-claim check (M3) tripped in none of them.
- 7 cases fail only because of faithfulness: G-FAC-010, G-FAC-014, G-FAC-018, G-FAC-020, G-TRB-005, G-TRB-006,
  G-TRB-008. Two of them (G-TRB-005, G-TRB-008) are confirmed judge errors above, and one (G-FAC-020) is a correct
  score. The other four were not examined.
- 33 of 48 faithfulness scores are exactly 0 or 1: short replies yield one or two claims, so one wrong verdict
  swings the score from 1.0 to 0.0.

## For the limitations section (10.1)

- The local 7B judge makes verdict errors on supported claims (negation, conditional claims, mixed Hindi and English
  replies). In the baseline run, the hallucination rate depends on faithfulness alone, so these errors raise it.
- Faithfulness is coarse for short replies (mostly 0 or 1), which makes it sensitive to a single wrong verdict.
- Earlier evidence: on G-UNA-001 the same judge gave appropriate refusal 1.0 to a reply that invents details,
  so it also produces false passes, not only false failures.
- These are the errors judge calibration (6.3.7, Phase 9) is meant to measure. Under its fixed policy, judge metrics
  get doubled margins or become advisory if Cohen's kappa is below 0.6 or 0.4.

## Judge stability test (6.3.5), same run

Ten judged cases, chosen with the judge seed, were each re-judged 3 times. The full output is in
`judge_stability_v1.0-20261103T152402.txt`.

- 18 of 26 metric measurements gave the same score all 3 times (spread 0).
- 5 of 10 cases had a spread below 0.1 on every metric.
- Largest spread: 1.0 (G-FAC-007 faithfulness and answer relevancy: 1.0, 0.0, 0.0).
- The pass/fail verdict changed between re-judgements in 5 measurements across 4 cases (G-FAC-010, G-NUM-003,
  G-FAC-007, G-FPR-004).

For the limitations section: temperature 0 and a fixed seed do not make this judge fully repeatable. Each DeepEval
metric makes several model calls, and a change in any one of them can flip a short reply's score between 0 and 1.
Because the judge runs on repeat 1 only (6.3.5), a single judgement decides the judge part of each case.
