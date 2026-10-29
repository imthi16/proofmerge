# Benchmark results

Each folder is one real run of `bench/run_bench.py`: Muse Spark 1.3 and Muse Wonder 1.0, probes run in the Docker sandbox. `results.json` holds every candidate's label, held-out flag, decision, reasons, model calls and tokens. It also records the run's provenance: ProofMerge git SHA, prompt hashes, model id and date.

These are 20 synthetic pull requests on one small fixture (`demo/demo-shop`). It is a smoke benchmark, not an accuracy claim.

## 2026-10-29-run1 (main `2d43fc4`)

| | BLOCK | REVIEW | PASS |
|---|---|---|---|
| Faulty, not held out (8) | 3 | 5 | 0 |
| Faulty, held out (4) | 3 | 1 | 0 |
| Safe, not held out (5) | 1 | 2 | 2 |
| Safe, held out (3) | 0 | 1 | 2 |

87 model calls, 71,211 prompt and 93,055 completion tokens.

What the run showed:
- No faulty case was passed.
- One safe case was blocked (`newsletter-promo-fix`). The hypothesis cited the implicit claim C0, but the behavior change it found is the one the PR's CHANGE claim asks for.
- Four faulty cases were REVIEW only because their intent text produced no explicit claim. The spec treats a C0 regression as a BLOCK candidate whenever an intent text is given.

Both issues were fixed after this run, so a later run on the same cases is not a clean held-out result.

Run 1 keeps only `results.json` here. Its evidence bundles held local paths, and editing them would break their hashes.

## 2026-10-29-run2 (main `bcd0032`, after the verdict fixes)

| | BLOCK | REVIEW | PASS |
|---|---|---|---|
| Faulty, not held out (8) | 8 | 0 | 0 |
| Faulty, held out (4) | 4 | 0 | 0 |
| Safe, not held out (5) | 1 | 2 | 2 |
| Safe, held out (3) | 0 | 1 | 2 |

90 model calls, 78,557 prompt and 98,448 completion tokens, 0 errors. Prompt hashes are unchanged from run 1. Every candidate's full evidence bundle is in this folder, and `proofmerge replay <candidate>/proof.json` checks it.

Compared with run 1, six faulty cases moved from REVIEW to BLOCK:
- Four of them (`boundary-qty-inclusive`, `ledger-mutation-order`, `promo-rule-order`, `zero-price-unknown`) are the intent-handling fix: a given intent with no explicit claim now lets a C0 regression BLOCK.
- Two (`price-error-type`, `order-total-off-by-one`) changed because Spark behaved differently between runs, not because of a rule change. In run 1 the hypothesis cited the vague CHANGE claim ("Refactor … for consistency"), which gives REVIEW. In run 2 it cited C0, which gives BLOCK. The verdict for these cases depends on which claim Spark cites. That is a known limit.

`newsletter-promo-fix` (safe) is still a false BLOCK. Muse Wonder agreed the probe tests a real change: legacy `subscribed` customers lose the promotion. It did not find a CHANGE quote that explicitly asks for that, so the strict rule keeps BLOCK. The rule stays strict on purpose, so that a vague sentence cannot hide a regression.

Because the fixes came after run 1, run 2 is not a clean held-out result, even for the cases marked held out.
