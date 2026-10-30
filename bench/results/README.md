# Benchmark results

Each folder is one real run of `bench/run_bench.py`: Muse Spark 1.3 and Muse Wonder 1.0, probes run in the Docker sandbox. `results.json` holds every candidate's label, held-out flag, decision, reasons, model calls and tokens. It also records the run's provenance: ProofMerge git SHA, prompt hashes, model id and date.

The runs use synthetic pull requests on one small fixture (`demo/demo-shop`). It is a smoke benchmark, not an accuracy claim.

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

## 2026-10-30-run3 (`5df3735d1de66590e00859b21af858eb5baaa3eb`)

Source: [run 3 results.json](2026-10-30-run3/results.json). Rules were frozen before batch 3 was written; the held-out slice is the clean number. PASS means no regression found within tested scope.

| Label and slice | BLOCK | REVIEW | PASS | ERROR |
|---|---:|---:|---:|---:|
| Faulty, all (16) | 14 | 2 | 0 | 0 |
| Safe, all (12) | 1 | 6 | 5 | 0 |
| Faulty, held out (8) | 6 | 2 | 0 | 0 |
| Safe, held out (7) | 0 | 4 | 3 | 0 |

The [run 3 results.json](2026-10-30-run3/results.json) records 121 model calls. The [baseline results.json](2026-10-30-baselines/results.json) records 28 model calls for B. Both files contain the same 28 candidates. A runs the base revision's committed tests against head in Docker; B is one Spark opinion per candidate. REVIEW is not counted as a BLOCK.

| Method | Faulty BLOCK / total | Faulty REVIEW | Safe BLOCK / total | Safe not BLOCK | Model calls |
|---|---:|---:|---:|---:|---:|
| Base tests on head (A) | 1 / 16 | — | 3 / 12 | 9 / 12 | 0 |
| One-shot Spark (B) | 16 / 16 | — | 0 / 12 | 12 / 12 | 28 |
| ProofMerge | 14 / 16 | 2 | 1 / 12 | 11 / 12 | 121 |

Baseline B scored every candidate correctly. This small synthetic bench has intents that name the changed function, so it does not separate the approaches on accuracy. ProofMerge's difference is evidence: a BLOCK is backed by a Spark-written probe passing base 3/3 and failing head 3/3, recorded and replayable with `proofmerge replay`; B gives an unverifiable opinion with no repro.

ProofMerge disagreements with labels, from [run 3 results.json](2026-10-30-run3/results.json):
- `default-shipping-express` (faulty, REVIEW): the probe may not test its hypothesis.
- `item-filter-stop` (faulty, REVIEW): the CHANGE claim is contested, and the model reply did not match CheckedProbes.
- `newsletter-promo-fix` (safe, BLOCK): a verified regression cites uncontested PRESERVE C0; a changed test's PASS outcome needs review.

Baseline A disagreements, from [baseline results.json](2026-10-30-baselines/results.json):
- `boundary-qty-inclusive`, `drop-tenant-validation`, `round-discount`, `mutate-stock`, `price-error-type`, `ledger-mutation-order`, `order-total-off-by-one`, `line-totals-generator`, `promo-rule-order`, `zero-price-unknown`, `default-shipping-express`, `coupon-lookup-exact`, `sku-list-order`, `cart-line-merge`, `item-filter-stop`: faulty, NOT_BLOCK because the existing tests passed on head.
- `intended-discount-rate`, `intended-express-fee`, `intended-free-shipping-threshold`: safe, BLOCK because the existing tests failed on head.

Baseline B has no disagreements with labels in [baseline results.json](2026-10-30-baselines/results.json).

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
