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

Only `results.json` is kept here. The per-candidate evidence bundles stay with the run, and their reproduce commands point at local paths.
