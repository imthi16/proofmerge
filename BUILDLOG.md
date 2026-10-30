# ProofMerge Build Log

Record what actually shipped; do not mark unimplemented features complete.

## 2026-10-27 — Planning and Claude project setup
- Repository already existed when Claude project preparation began.
- Added initial Claude Code instructions, project spec, sprint plan, Claude Project setup guidance, originality declaration and ignore rules.
- Status: planning/docs setup only. Meta integration, execution sandbox, benchmarks and proof reports remain unimplemented until tested.

### Verification
- GitHub repository exists and is public: https://github.com/imthi16/proofmerge
- Future implementation test results: **not yet recorded**

### Meta API access check (2026-10-27)
- Authenticated `GET https://api.meta.ai/v1/models` with `MODEL_API_KEY` succeeded (run by the author).
- Model IDs returned: `muse-spark-1.3`, `muse-spark-1.3-contributor`, `muse-spark-1.2`, `muse-spark-1.2-contributor`, `muse-spark-1.1`, `muse-wonder-1.0`, `muse-image-1.0`, `muse-voice-transcribe-1.0`, `sam-3.1`.
- ProofMerge will use `muse-spark-1.3` and `muse-wonder-1.0`; `-contributor` IDs will be rejected by config.

### Day 2 gate: real authenticated Spark response (2026-10-27)
- `proofmerge smoke` made one real `muse-spark-1.3` Chat Completions call with json_schema structured output.
- Output: `muse-spark-1.3: ok=True message='proofmerge smoke test'`, `tokens: prompt=95 completion=168`, exit 0. Approx. cost at list price: under $0.001.
- Offline checks: `ruff check` clean, `pytest -q` 9 passed (fake client, no network).
- Also added and verified: package/CLI skeleton, demo-shop fixture with three candidate PRs (`demo/check_demo.py` passes), Meta API notes with quotes and URLs (`docs/META_API_NOTES.md`).

## 2026-10-28 — Day 3: intent contract and Spark hypotheses

### Day 3 gate: fixture diff + intent → quote-checked claims + a hypothesis tied to a claim
- Command: `proofmerge hypotheses demo/.build/demo-shop --base main --head candidate/tenant-cache-regression --intent-file demo/candidates/tenant-cache-regression/intent.md`
- Real `muse-spark-1.3` run: 2 calls, tokens prompt=1452 completion=4468, exit 0. Base `1e6c838`, head `6796cac`, diff sha256 `6d9c5b97…c252`.
- Intent claims: C0 (implicit PRESERVE), C1 CHANGE "Add caching to price lookup for speed.", C2 PRESERVE "Returned prices must not change." Both quotes passed the substring check; 0 rejected.
- Hypotheses: 3 kept, 0 rejected.
  - H1 (C2): the cache is keyed by SKU only, so tenant-b gets tenant-a's price. This is the planted bug.
  - H2 (C0): a cache hit skips the unknown-tenant check.
  - H3 (C0): a cache hit leaks a price for a SKU the tenant does not have. The fixture data cannot show H3 yet, because both tenants sell `widget`.
- The non-recursive `$defs`/`$ref` response schema was accepted by the Meta API.
- Offline checks: `ruff check` clean, `pytest -q` 38 passed.

### T11 benchmark batch 1 (pulled forward from Nov 2)
- Created `bench/candidates/` with 8 new PR overlays against `demo/demo-shop`:
  - 5 faulty: `boundary-qty-inclusive`, `drop-tenant-validation`, `round-discount`, `mutate-stock`, `skip-boundary-test`.
  - 3 safe: `pure-refactor-constants`, `intended-discount-rate`, `cache-with-tenant-key`.
- Created hidden ground truth in `bench/ground_truth/` with one probe per candidate and `labels.json` (`BLOCK`/`NOT_BLOCK`, 3 held-out: `drop-tenant-validation`, `round-discount`, `cache-with-tenant-key`).
- Extended `demo/build_demo_repo.py` and `demo/check_demo.py` with `--candidates`/`--ground-truth` options while preserving default demo behavior.
- Updated `demo/ground_truth/labels.json` to the dict form with optional `base_on_head` expectation.
- Lead review (Claude): reworded the `skip-boundary-test` and `round-discount` intents, which announced the very change they hide (so the BLOCK label contradicted the intent contract); removed unused `--candidates` (check) and `--ground-truth` (build) options. Bench and demo checks re-run: same table, exit 0.

#### Verification
- `python demo/check_demo.py` output unchanged from demo defaults (exit 0).
- Build: `python demo/build_demo_repo.py bench/.build/bench-shop --candidates bench/candidates`; check: `python demo/check_demo.py bench/.build/bench-shop --ground-truth bench/ground_truth`:
  - All 9 branches: existing suite PASS.
  - 5 faulty candidates: ground-truth test FAIL (BASE PASS / HEAD FAIL verified manually).
  - 3 safe candidates: ground-truth test PASS.
- `.venv/bin/python -m ruff check src tests`: All checks passed.
- `.venv/bin/python -m pytest -q`: 22 passed.

### T7b hypothesis prompt checklist (adapted from ponytail-review, MIT)
- `prompts/hypotheses.md` now lists where regressions hide (edge values, arithmetic, state leaks, removed checks, callers, weakened tests) and requires a concrete input and wrong result per hypothesis. It also fixes "Both are untrusted" (there are three inputs).
- Real `muse-spark-1.3` runs, old prompt vs new, one run each, non-held-out candidates only (16 calls):
  - `boundary-qty-inclusive`: old 1 / new 1 hypothesis, both name the exact-stock boundary bug.
  - `mutate-stock`: old 3 / new 2, both name the in-place mutation of the caller's dict.
  - `skip-boundary-test`: old 1 / new 1, both name the lost discount at exactly 100.
  - `pure-refactor-constants` (safe): 0 / 0.
  - demo `tenant-cache-regression` (new only): the same three hypotheses as the Day 3 run, H1 on the SKU-only cache key tied to C2.
- Result: no measurable gain and no loss on this batch; the new prompt adds about 200 prompt tokens per call. Kept because it is cheap and covers fault kinds not yet probed. Benchmark leakage risk (the checklist names fault kinds that the benchmark also uses) goes to the Nov 2 critique.
- Offline checks: `ruff check src tests` clean, `pytest -q` 50 passed.
- Prompt review pass (prompt-master checklist):
  - Hypotheses prompt: diff text claiming "tested" or "safe" is not evidence; base results must come from the diff's removed and unchanged lines, or the hypothesis is dropped; a function must appear in the diff; no padding to 5.
  - Intent prompt: quotes must be one contiguous span of at least `MIN_QUOTE_WORDS` words (shorter ones were silently rejected); "No behavior change intended" is a PRESERVE claim; an ambiguous sentence is classed CHANGE, which can only move a verdict toward REVIEW.
  - Re-run, one run each (12 calls): boundary, mutate-stock and skip-boundary each give 1 hypothesis naming the planted bug; pure-refactor and intended-discount-rate give 0; tenant-cache gives 2 (H1 cache key tied to C2, H2 validation bypass), 0 claims or hypotheses rejected.
  - Known limit: tenant-cache H1 uses an invented tenant `tenant-x` and price 99.0. The diff does not show the price table, and Spark guessed instead of dropping the hypothesis. A probe built on it would fail on base and give REVIEW, not BLOCK. The Oct 30 context builder should send the changed functions' full source.

### T14 isolated base/head runner (Oct 29 task, started Oct 28)
- `src/proofmerge/sandbox.py`:
  - Each revision is exported with `git archive` into a fresh directory, never a worktree of the user's repo. Extraction uses tarfile's `data` filter. Only full commit SHAs are accepted, so a ref like `--output=...` can never reach git as an option.
  - Probes run in Docker image `proofmerge-runner:0.1` (python:3.13-slim + pytest 9.1.1, built from `docker/runner/Dockerfile`).
  - Container settings: `--network none`, read-only root and code mounts, tmpfs `/tmp`, `--cap-drop ALL`, `no-new-privileges`, uid 65534, 64 pids, 512 MB memory, 1 CPU, 60 s timeout (the container is killed by name).
  - The container gets no host environment, so `MODEL_API_KEY` cannot leak into it.
  - Each probe runs 3 attempts per revision, and every attempt is recorded as an `Execution`: exit code, outcome, duration, stdout/stderr files, image ID. Only pytest exit 1 counts as FAIL; any other exit code is ERROR.
- Gate, real Docker run on the demo fixture: the ground-truth tenant-cache probe gives base PASS 3/3 and head FAIL 3/3 (exit 1, `assert 25.0 == 35.0`, i.e. tenant-b got tenant-a's price). About 0.5 s per attempt.
- Isolation probe, run inside the container: it has no `MODEL_API_KEY` although the host had one set, a socket connect fails, writing to the code mount fails, and it does not run as root. All pass.
- `evidence.Execution` gains an optional `runner_image_digest`, which is None only in the hand-made samples.
- Offline checks: `ruff check src tests` clean, `pytest -q` 69 passed, including the 2 Docker tests. Those tests skip when Docker or the image is missing.
- Not done yet:
  - The failure type is not parsed, so a NameError raised inside the test body still reads FAIL (collection errors are already ERROR). Oct 30 probe validity must check this.
  - No per-run wall-clock budget across probes.
  - The base image is not pinned by digest (marked `shortcut:`).

### T13 report.html — OpenCode · review: Claude · review 2026-10-28
Branch `task/T13-report-html` (from `task/T12-proof-schema`).
- `src/proofmerge/report.py`: `render_html(proof, evidence_dir)` — stdlib only (`html`, `shlex`, `pathlib`), inline CSS, no JavaScript, no external resources. Every proof.json string is escaped; probe paths are rejected if they resolve outside the evidence dir.
- CLI: `proofmerge report <proof.json> --out report.html` (evidence dir = the folder holding proof.json).
- Page order per task: verdict banner (decision as text + reasons; PASS says "No regression found within the tested scope") → intent claims with exact quotes + rejected claims → tamper findings → hypothesis cards (cited claim, expected base/head, base/head × attempt grid, classification, probe source or "probe source not captured", reproduce command) → provenance (base/head SHA, diff sha256, probe sha256s, model usage) → scope limitations + unresolved risks, always visible.
- `evidence.py` contract needed no changes for this task.
- Screenshots for the PR (light/dark × desktop/1280 × 375px, both samples): rendered headlessly and copied to `../t13-screenshots/` next to the repo (not committed).

#### Verification
- `.venv/bin/ruff check src tests`: All checks passed.
- `.venv/bin/pytest -q`: 63 passed (10 new in `tests/test_report.py`: both samples' decision/claim ids/hypothesis ids/quotes, escaped `<script>` claim, probe source captured/missing/outside-evidence-dir, PASS wording, CLI success + missing-file error).
- Both reports opened and visually checked (banner text, grid, monospace hashes, dark via `prefers-color-scheme`, 375px layout).

### T13b report.html follow-up (Claude took it over from OpenCode)
- The H1 card now cites the claim's verified quote, not Spark's paraphrase, because only the quote is checked against the intent.
- The card shows "Why head failed": the pytest `E ` lines of the first failing head run, at most 20 of them. They are read with the same evidence-dir path guard as the probe source.
- Grid rows carry short SHAs, and the banner shows `run_id`, so a page rendered from a `sample-` proof says so.
- Sample proofs now use the hypotheses from a real Spark run whose H1 uses the fixture's real tenants. The earlier sample's H1 used an invented `tenant-x`.
- The sample probe is the demo's ground-truth test, and `tests/fixtures/H1/head-1.stdout` is a real Docker run's head output (`assert 25.0 == 35.0`).
- README has a report section with light/dark screenshots taken with headless Chrome, labeled as rendered from the sample proof, and an updated "What works today" table.
- `ruff check src tests` clean; `pytest -q` 116 passed.

### Next task
- Oct 29: base/head worktrees and isolated test execution with hashes.
- T8 test-tamper static checks (Codex) is still open.


### T8 test-tamper static checks — Codex, 2026-10-28
- Branch: `task/T8-tamper-static`; task status: review (Claude). No commit, push or merge.
- Added `src/proofmerge/tamper.py`: `find_tamper(repo_path, context)` uses T2's changed-file list and resolved base/head SHAs. Full committed Python test sources are read with Git and parsed with `ast`; a normal diff alone omits enclosing functions, imports and decorators.
- Reports deleted files/functions, new skip/xfail markers, removed assertion statements, and modified files/functions. Supports imported marker aliases, module/class pytestmark, async tests and unittest.TestCase methods. Static findings leave `base_test_on_head_outcome` unset; no model calls, test execution or verdict decisions occur in this module.
- Parse, encoding and Git read failures raise errors instead of returning a clean result. Dirty working files do not replace committed source.
- Scope limit: default pytest Python filenames (`test_*.py`, `*_test.py`), ordinary test functions/classes and statically recognizable pytest/unittest marks. Custom collection rules and dynamic marker construction are not interpreted. Base-test execution on head remains future sandbox work.
- Acceptance tests use owned temporary Git repositories; fixture test source is never imported or executed.
- Existing edits in `CLAUDE.md`, `docs/PROJECT_SPEC.md`, and `docs/SPRINT_PLAN.md` preserved. `TASKS.md` is locally excluded by `.git/info/exclude`; its review status and follow-up (now T15) were updated on disk.

#### Verification (project .venv on PATH; PYTEST_ADDOPTS cleared for pytest summaries)
```text
$ pytest -q tests/test_tamper.py
.........................                                                [100%]
25 passed in 2.12s

$ ruff check src tests
All checks passed!

$ pytest -q
........................................................................ [ 96%]
...                                                                      [100%]
75 passed in 2.56s
```
All three commands exited 0. `graphify update .` exited 0 (no code-graph topology changes detected). `git diff --check` passed.

#### Lead review (Claude)
- Moved from the main folder into its own worktree and branch on top of T12, so it now uses the shared `evidence.TamperFinding` (`test_name`) instead of a second model.
- ASSERTION_REMOVED now means the number of asserts went down. Before, any changed assert counted: the demo's `== 108.0` → `== 90.9` rewrite was reported as a removed assertion. A test for that case fails on the old rule.
- On the demo: test-tamper reports the file MODIFIED, `test_bulk_discount_at_threshold` DELETED and `test_bulk_discount_above_threshold` MODIFIED; tenant-cache reports nothing.
- `ruff check src tests` clean; `pytest -q` 79 passed.

#### Separate GitHub reviews
- Reviewed PR #7 at `c810f88a6697e2746d418f2bf062837430c798de` and PR #8 at `40568ff7cfb1f26fb8c8be256bcc71e3636b2443`; GitHub reports both merged already. No GitHub comments or reviews submitted.
- PR #7 finding: `docs/PRIOR_ART.md` describes repeated runs and replay bundles as implemented, although the revision has no replay command. Reproducer: `proofmerge replay /tmp/proofmerge-review-nonexistent.json` exits 2 with `No such command 'replay'.` Follow-up recorded as T15.
- PR #8: no concrete failing case introduced by the final changes found. Literature checks used the linked primary arXiv abstracts and ORION v1 text; no additional source discrepancy established.

### T18 Spark probe loop — Claude, 2026-10-28
- `src/proofmerge/probes.py` + `prompts/probe.md`: for each hypothesis, Spark writes one pytest probe. It runs once on base only. If it does not pass, Spark gets that base run's output tail and revises the probe, up to 3 drafts. A probe that passed on base then runs 3 times on base and 3 on head. Spark never sees head results.
- Spark gets the base source of every changed file (up to 60,000 chars), so it no longer has to guess values the diff does not show. This fixes the T7 limit where the price was invented.
- The loop is a sequence of schema-validated JSON calls, using a new `history` argument on `MetaClient.structured`, instead of native tool calling. The pipeline, not the model, decides when a probe runs, and every reply is validated.
- `sandbox.py`:
  - `run_experiment(..., head_sha=None)` runs base only.
  - A fixed `conftest.py` is written next to every probe. A failure other than an assertion or `pytest.raises` (for example NameError, ImportError or a setup error) sets exit 6, which reads ERROR. This closes the T14 gap where a NameError counted as FAIL. Checked in Docker.
- Private-name guard. The first real run on the safe bench candidate `cache-with-tenant-key` gave 2 false regressions (base PASS 3/3, head FAIL 3/3). Both probes edited the private `_PRICES` table and then expected the cache to return the new value; serving the cached value is the caching the PR asked for. Now a draft that uses a `_private` attribute, import, or `getattr`/`setattr` string is sent back to Spark without being run. Probes test public behavior only.
- Real runs (Spark `muse-spark-1.3`, Docker runner), after the guard:
  - `tenant-cache-regression`: H1 (unknown tenant served from the SKU-only cache) and H2 (tenant-a gets tenant-b's price, `assert 35.0 == 25.0`) both give base PASS 3/3 and head FAIL 3/3 with exit 1. Each probe passed on base at the first draft. 4 calls, 4,526 prompt / 8,754 completion tokens.
  - `cache-with-tenant-key` (safe): Spark returned no probe for either hypothesis, so nothing reached head and there is no false regression. 4 calls, 4,506 / 5,851 tokens.
- New CLI command: `proofmerge probe <repo> --base --head --intent-file --evidence-dir`. It writes `probe-run.json`, the probes, and the logs of every draft and run.
- Checks: `ruff check src tests` clean; `pytest -q` 117 passed, including the Docker tests.
- Known limits:
  - One real run per candidate; hypotheses vary between runs.
  - The private-name guard is a heuristic against an honest model overreaching, not a sandbox. A name built at runtime still gets through, so the verdict must not rely on it.
  - There is no classification or verdict yet; that is Oct 31.
  - The fixed conftest means a repository's own conftest is not loaded (same as T16).

#### Codex review fixes (PR #14)
- The private-name guard missed `vars(pricing)['_PRICES']` and `pricing.__dict__[...]`. Codex showed that such a probe recreates the false regression on `cache-with-tenant-key`. Now any string constant with a `_private` part is flagged, and so are `vars`, `__dict__`, `globals` and `locals`.
- A probe whose tests were skipped exited 0, so it counted as passing on base without checking anything. The outcome plugin now turns any skip, xfail or xpass into exit 6 (ERROR); the constant is renamed `INVALID_PROBE_EXIT`. A module-level `importorskip` already exited 5 (ERROR); a test now covers it.
- `ruff check src tests` clean; `pytest -q` 122 passed, including the Docker tests. The real Spark runs above were not repeated after these fixes.

### T16 dynamic test-tamper checks — 2026-10-29
- Encoding review fix: normalize committed base-test encoding declarations for explicit UTF-8 probe writes; added an offline Latin-1 `café` regression test. Verification: 155 tests passed in 21.10s; Ruff passed.
- Added `run_base_tests_on_head`: each affected file's committed base test source runs three times on base and three on head through the sandbox, including files deleted on head. Experiments use T1, T2, …; all findings for one file share its result. FAIL requires base PASS 3/3 and head FAIL 3/3; head PASS 3/3 gives PASS; other execution outcomes give ERROR. No verdict decision or model call.
- Offline coverage checks repeatability, errors/timeouts, deleted files, committed source, per-file deduplication, distinct experiment IDs, and newly added tests (no base source → ERROR without execution).
- Docker was reachable with `proofmerge-runner:0.1`. Demo acceptance tests passed: `test-tamper` and `intended-change` each produced FAIL; `tenant-cache-regression` produced no findings or experiments.
- Known limits: repository conftest fixtures are not loaded, so tests needing them yield ERROR; skip/xfail/xpass also yield ERROR. The existing archive helper cannot read a completely empty Git tree; Git/export/infrastructure exceptions propagate. The first full run reported `11 failed, 143 passed in 19.69s` because the new deleted-file fixture left an empty tree; retaining a README corrected the fixture. The first Ruff run found one import-order issue, corrected before the final checks.
- Final verification (both commands exited 0):

```text
$ PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q
........................................................................ [ 46%]
........................................................................ [ 93%]
..........                                                               [100%]
154 passed in 19.02s

$ ../proofmerge-T3/.venv/bin/ruff check src tests
All checks passed!
```

`git diff --check` passed. `graphify update .` exited 0.


### T21 Evidence manifest and replay — 2026-10-29
- Added sorted SHA-256 manifests, detection of missing/changed/unlisted files, and rejection of symlinks and unsafe listed paths. Missing or invalid manifests return a problem instead of raising.
- Added `proofmerge replay <proof.json>`: validates the proof and checks both manifest hashes and every experiment's recorded probe hash without executing probes. `probe` writes its manifest after `probe-run.json` as its final file write.
- Added 23 tests covering integrity changes, malformed manifests, traversal, symlinks, replay exit codes, independent probe hashes and probe manifest integration.
- Verification (both commands exited 0):
```text
$ PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q
........................................................................ [ 45%]
............................................sss............s............ [ 90%]
................                                                         [100%]
156 passed, 4 skipped in 5.77s

$ ../proofmerge-T3/.venv/bin/ruff check src tests
All checks passed!
```

- Docker was unreachable in this sandbox; the four Docker integration tests were skipped. Direct image inspection exited 1 with `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`.
- Limits: hashes detect changes but are unsigned; replacing both proof and manifest is not authenticated. Checks assume a stationary evidence bundle, without concurrent filesystem modification. Replay does not re-run experiments or call models.

### T25 Evidence normalizer — 2026-10-29
- Added deterministic classification requiring exactly three agreeing PASS/FAIL runs per revision. Findings link to hypothesis claims; verification requires a regression, a matching base-passing probe, and a 64-character hexadecimal source hash. Non-H experiments are skipped; unknown H ids raise `ValueError`.
- Hand-built experiment tests cover all classifications, disagreement, errors, timeouts, run counts, verification gates, claim mapping, and tamper filtering. Findings advertise `proofmerge report`, which the CLI implements; hash-checking replay remains a separate task.
- Checks (exit 0 for both): `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` → `199 passed, 4 skipped in 6.35s`; `../proofmerge-T3/.venv/bin/ruff check src tests` → `All checks passed!`. For pytest, cleared the inherited `PYTEST_ADDOPTS=-q` to show the summary and used a temporary PATH containing Git but no Docker; the four Docker integration tests skipped without invoking Docker.

### T17 benchmark batch 2 — OpenCode, 2026-10-29
- Branch `task/T17-bench-batch2` from main 72ee0ce. Added 7 faulty + 5 safe bench candidates against `demo/demo-shop`, so the bench reaches 12 faulty + 8 safe, plus a new base module `orders.py` (order totals, per-line totals, customer ledger, promotions) with its own passing test file in the base.
  - Faulty: `price-error-type` (unknown SKU raises LookupError instead of ValueError), `ledger-mutation-order` (rejected purchase mutates the ledger before the negative-amount validation), `line-totals-generator` (list return becomes a generator), `order-total-off-by-one` (positional-iteration rewrite drops the last line), `promo-rule-order` (rule table lists news-5 before vip-10 and both still read `subscribed`, so dict order lets the newsletter promotion win over VIP), `zero-price-unknown` (price lookup swallows unknown tenant/SKU and returns 0.0), `default-shipping-express` (default shipping argument changed to "express").
  - Safe: `extract-inventory-validation` (checks extracted to a helper), `comprehension-line-totals` (loop → list comprehension, still a list), `intended-express-fee` (express fee 12 → 15 with updated tests and an explicit change intent, ground truth FAIL on base / PASS on head), `single-lookup-pricing` (.get rewrite, same exceptions and messages), `newsletter-promo-fix` (promotion checked against the renamed signup key, ground truth FAIL on base / PASS on head).
  - Base docstring of `promo_for` already promised newsletter subscribers the promotion; the base code reads the pre-rename field, so the fix restores documented behavior.
- Leakage rules (T19) applied: new module fixtures and every patched file keep docstrings identical to base except where the change adds a helper; all 12 new intents are ticket-free, 1–3 sentences (varied wording, none uses the retired "No behavior change intended" sentence, none states its planted bug); the two held-out faulty kinds are absent from `src/proofmerge/prompts/hypotheses.md` "Where regressions hide" (dict iteration order, default argument changed); held-out safe: `extract-inventory-validation`, `single-lookup-pricing`; no skip/xfail markers introduced.
- Known fixture quirk: every base `order_total` test ends in a cancelled line with zero quantity (the module documents that cancellations are appended last), which keeps the suite green under the off-by-one head; the hidden probe uses two non-cancelled lines.

#### Verification
- Build and check (bench, 20 candidates + main):
  - `python demo/build_demo_repo.py bench/.build/bench-shop --candidates bench/candidates`
  - `python demo/check_demo.py bench/.build/bench-shop --ground-truth bench/ground_truth` (exit 0):

```text
branch                              existing suite  ground-truth test  base tests on head
--------------------------------------------------------------------------------------
main                                PASS            PASS               n/a
candidate/boundary-qty-inclusive    PASS            FAIL               PASS
candidate/cache-with-tenant-key     PASS            PASS               PASS
candidate/comprehension-line-totals PASS            PASS               PASS
candidate/default-shipping-express  PASS            FAIL               PASS
candidate/drop-tenant-validation    PASS            FAIL               PASS
candidate/extract-inventory-validation PASS         PASS               PASS
candidate/intended-discount-rate    PASS            PASS               FAIL
candidate/intended-express-fee      PASS            PASS               PASS
candidate/ledger-mutation-order     PASS            FAIL               PASS
candidate/line-totals-generator     PASS            FAIL               PASS
candidate/mutate-stock              PASS            FAIL               PASS
candidate/newsletter-promo-fix      PASS            PASS               PASS
candidate/order-total-off-by-one    PASS            FAIL               PASS
candidate/price-error-type          PASS            FAIL               PASS
candidate/promo-rule-order          PASS            FAIL               PASS
candidate/pure-refactor-constants   PASS            PASS               PASS
candidate/round-discount            PASS            FAIL               PASS
candidate/single-lookup-pricing     PASS            PASS               PASS
candidate/skip-boundary-test        PASS            FAIL               FAIL
candidate/zero-price-unknown        PASS            FAIL               PASS
```

- Ground truth on main vs each new candidate branch (exit 0 script, `PYTHONDONTWRITEBYTECODE=1`):

```text
candidate                        gt on main (base)  gt on head
price-error-type                 PASS               FAIL
ledger-mutation-order            PASS               FAIL
line-totals-generator            PASS               FAIL
order-total-off-by-one           PASS               FAIL
promo-rule-order                 PASS               FAIL
zero-price-unknown               PASS               FAIL
default-shipping-express         PASS               FAIL
extract-inventory-validation     PASS               PASS
comprehension-line-totals        PASS               PASS
intended-express-fee             FAIL               PASS
single-lookup-pricing            PASS               PASS
newsletter-promo-fix             FAIL               PASS
```
  - 7 faulty: base PASS / head FAIL. 3 safe: base PASS / head PASS. Intended fee change and the bug fix: base FAIL / head PASS.

```text
$ PYTEST_ADDOPTS= ../proofmerge-T3/.venv/bin/python -m pytest -q
137 passed in 11.94s

$ ../proofmerge-T3/.venv/bin/ruff check src tests
All checks passed!
```
- Demo check rebuild + `python demo/check_demo.py` (exit 0): main PASS/PASS; intended-change PASS / n/a / FAIL; tenant-cache-regression PASS/FAIL/PASS; test-tamper PASS/FAIL/FAIL — same table as before batch 2.
- Not verified here: no Spark probe runs against the new candidates yet (that is the batch-2 benchmark run, not the fixture task); TASKS.md is outside this worktree and was not edited by this agent.


### 2026-10-29 — T24 deterministic verdict engine
- Added `decide` with rules 1–9, BLOCK precedence with all reasons retained, scope limitations, unresolved hypotheses, and unknown-claim rejection; tests cover every rule and the three hand-built demo cases.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: `268 passed, 7 skipped in 7.06s`. Docker was excluded from PATH; the seven Docker integration tests were skipped.
- `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`

### 2026-10-29 — T23 end-to-end verification
- Added `proofmerge verify`: resolves revisions, extracts intent, generates and runs probes, checks changed tests, normalizes findings, and writes `proof.json`, `report.html`, then the manifest as the final write.
- Added 31 acceptance and failure-path tests, including the real Docker demo producing base PASS 3/3, head FAIL 3/3, BLOCK, and an intact replay bundle. All model replies in tests are scripted.
- API/client-setup, sandbox and tamper-stage failures are recorded as REVIEW reasons and completed evidence is kept. They never erase hard evidence: a verified base PASS 3/3 / head FAIL 3/3 regression, or a base test failing on head with no CHANGE claim, still gives BLOCK (the verdict engine's precedence is unchanged). A run that fails before any such evidence exists gives REVIEW.
- Verification:
  ```text
  $ PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q
  306 passed in 24.97s (Docker tests included)

  $ ../proofmerge-T3/.venv/bin/ruff check src tests
  All checks passed!
  ```
- Real run, 2026-10-29 (Muse Spark, Docker), `proofmerge verify <demo repo> --base main --head candidate/tenant-cache-regression --intent-file demo/candidates/tenant-cache-regression/intent.md`:
  - Claims: C1 CHANGE "Add caching to price lookup for speed", C2 PRESERVE "Returned prices must not change".
  - H1 (C0) and H2 (C2), both Spark-written probes: base PASS 3/3, head FAIL 3/3, `AssertionError: assert 25.0 == 35.0` (tenant-b got tenant-a's price). Verdict BLOCK (the exit code was not captured in this run; exit codes are covered by the CLI tests). 4 model calls, 4,552 prompt / 7,683 completion tokens.
  - `proofmerge replay` on the bundle: `evidence intact: 37 files`.
  - Known limit seen in this run: H2's title is "cached price goes stale", but its probe checks tenant isolation, the same bug as H1. A probe is not yet checked against its hypothesis text.

### T22 Benchmark and demo leakage cleanup — 2026-10-29
- Neutralized cache docstrings and the boundary-test skip reason; removed ticket/approval cues and mutation disclosure from intents while preserving explicit intended changes and the newsletter fix description. Labels and candidate executable logic are unchanged. Added a separate tenant-b price assertion to the tenant-validation ground truth.
- Build/check commands passed (exit 0): `PY=../proofmerge-T3/.venv/bin/python`; `B=$(mktemp -d)/bench && $PY demo/build_demo_repo.py $B --candidates bench/candidates && $PY demo/check_demo.py $B --ground-truth bench/ground_truth`; `D=$(mktemp -d)/demo && $PY demo/build_demo_repo.py $D && $PY demo/check_demo.py $D`.
- Compared against a `git archive` of main at `e188f4b`: the 20-candidate bench table and the 3-candidate demo table are identical before and after (lead re-run, all four checks exit 0).
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (exit 0): `238 passed, 7 skipped in 8.19s`.
- `../proofmerge-T3/.venv/bin/ruff check src tests` (exit 0): `All checks passed!`; `git diff --check` passed.
- Intent prompt (`intent.py`): the PRESERVE rule no longer quotes the benchmark's own sentence "No behavior change intended"; it now says a general statement that results or behavior do not change is a PRESERVE claim. The hypotheses prompt is unchanged; its sha256 before benchmark runs is `7b7bde5be0de5a370da585ee767be267034ca79b9a0c4aec86788e480ba04309`. After this edit `pytest -q`: 245 passed (Docker tests included); `ruff check src tests`: clean. Real Spark runs were not repeated after the prompt change.

### 2026-10-29 — T28 GitHub composite action
- Added the composite action, workflow documentation, and 25 offline tests covering credential skips, trusted action installation paths, literal PR input, exit capture, artifact publication configuration, comment creation/update, malformed evidence, and verdict policy.
- Empty keys skip all work and comments. Missing evidence or inconsistent verify exits fail; BLOCK always fails, and REVIEW fails with `fail-on: review`. Marked bot comments are updated using a JSON input file; new comments use a body file.
- Verification:
  ```text
  $ PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q
  323 passed, 8 skipped in 11.80s

  $ ../proofmerge-T3/.venv/bin/ruff check src tests
  All checks passed!
  ```
- The eight Docker integration tests were skipped by their Docker/image availability guard. No real model calls or GitHub execution were performed. Real GitHub validation remains pending.

### 2026-10-29 — T29 benchmark runner
- Added candidate filtering, bounded per-candidate clients, evidence paths, usage totals, provenance hashes, and results.json. Reports separate held-out counts and retains candidate errors without stopping the batch.
- Offline tests cover all label/outcome combinations, selection, provenance, failure continuation, output preservation, and a real fixture-repository build with verification stubbed.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: `312 passed, 8 skipped in 9.41s`.
- `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`; `../proofmerge-T3/.venv/bin/ruff check bench/run_bench.py`: `All checks passed!`.
- Full bench lint reports nine existing import-order errors in fixture files; those files were left unchanged. No real model benchmark was run.

### 2026-10-29 — T30 independent cross-checks
- Added independent claim and probe checks, conservative verdict qualifications, report annotations, sorted proof fields with empty defaults, and a two-call budget allowance. Disagreement cannot create a BLOCK or PASS; unrelated hard evidence retains BLOCK precedence.
- Offline tests cover agreement, disagreement, omissions, conflicting duplicates, input separation, schema validation, failure recording, proof bundles, budgets, and compatibility with older proofs. No real model calls were made.
- Verification (extra quiet environment setting cleared for the test summary):
  ```text
  $ PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q
  386 passed, 8 skipped in 22.54s

  $ ../proofmerge-T3/.venv/bin/ruff check src tests
  All checks passed!
  ```
- The eight Docker tests were skipped by their availability guard. `ruff check bench/run_bench.py` and `git diff --check` also passed.
- Real runs after this change, 2026-10-29 (Muse Spark + Muse Wonder 1.0, Docker), on the three demo candidates:
  - tenant-cache-regression: BLOCK, exit 2. H1 (C2) and H2 (C0) verified regressions. Wonder marked H3's probe as not testing its hypothesis, so H3 alone is REVIEW. No claim contested. 7 calls, 6,970 prompt / 10,674 completion tokens.
  - test-tamper: BLOCK, exit 2. H1 (C1) verified regression plus the tamper check. Nothing contested or mismatched. 5 calls.
  - intended-change: REVIEW, exit 0, from the CHANGE claim and the changed tests. 3 calls.
  - Full suite after the change: `pytest -q` 394 passed (Docker tests included); `ruff check src tests` clean.


### 2026-10-29 — T30 review fixes
- Probe mismatches now require REVIEW for every finding classification, including probes passing on both revisions. Claim-check errors no longer skip the independent probe check; unrelated verified regressions still BLOCK.
- Added regression coverage for rejected and omitted passing probes, all finding classifications, API/schema claim-check failures, and preserved BLOCK precedence. No real model calls were made.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (extra quiet environment setting cleared): `399 passed, 8 skipped in 20.18s`. The eight skips are Docker availability guards.
- `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`; `git diff --check` passed.

### 2026-10-29 — T31 Fresh install + `proofmerge setup`
- `pyproject.toml` now force-includes `docker/runner/Dockerfile` into the wheel as `proofmerge/runner/Dockerfile`; prompts are already packaged.
- `proofmerge setup` builds `proofmerge-runner:0.1` from the packaged Dockerfile, located with `importlib.resources` and falling back to the repo's `docker/runner` when running from source. Docker is invoked with an argv list; missing Docker or a failed build exits 1 with a clear message. On success it prints the image id.
- `sandbox.image_digest` now tells the user to run `proofmerge setup` when the image is missing.
- README has a new "Install" section with `uv tool install`, `proofmerge setup`, a demo `verify` example, and the Docker/Python/MODEL_API_KEY requirements.
- Added `tests/test_setup.py`: packaged Dockerfile resolution, source-checkout fallback, argv-list build call, missing-Docker and build-failure exits, and the updated image-not-found message.

#### Verification
- Wheel contents (`../proofmerge-T3/.venv/bin/python -m pip wheel . --no-deps -w /tmp/pm-wheel`):
  ```text
  proofmerge/prompts/hypotheses.md
  proofmerge/prompts/probe.md
  proofmerge/runner/Dockerfile
  ```
- Fresh venv install (`python3 -m venv /tmp/pm-venv && /tmp/pm-venv/bin/pip install /tmp/pm-wheel/proofmerge-*.whl`) succeeded.
- `/tmp/pm-venv/bin/proofmerge --help` lists `setup` and `verify`.
- `/tmp/pm-venv/bin/proofmerge setup` printed `built runner image: sha256:7174905168e20eba95778e38864657946941e275dedc412a3ddf07750ced7c69`.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: 331 passed, 8 skipped in 12.07s.
- `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`.

### Benchmark run 1 and doc status refresh — 2026-10-29
- First real run of `bench/run_bench.py` on main `2d43fc4`: Muse Spark 1.3 + Muse Wonder 1.0, Docker sandbox, 20 candidates, 87 model calls, 0 errors. Faulty: 6 BLOCK, 6 REVIEW, 0 PASS. Safe: 1 BLOCK, 3 REVIEW, 4 PASS. Counts split by held-out status are in `bench/results/README.md`; per-candidate data is in `bench/results/2026-10-29-run1/results.json`, with local evidence paths replaced by `<run dir>`.
- Two verdict issues found: a false BLOCK where a CHANGE claim explains the change (`newsletter-promo-fix`), and four faulty cases held at REVIEW because an intent text with no explicit claim was treated as no intent. Changes made after this run address both, so later runs on these cases are not clean held-out results.
- `docs/PRIOR_ART.md` and `ORIGINALITY.md` now mark the verdict engine, probes, repeated runs, manifest replay (hash checks only) and tamper checks as built. Replay re-running the experiments stays planned.

### 2026-10-29 — T36 Verdict fixes
- Intent text with no explicit claims now leaves a verified C0 regression eligible for BLOCK. Missing or blank intent, and contracts whose proposed claims were all rejected, keep that regression at REVIEW.
- Wonder checks whether a verified CHANGE quote explicitly requests the behavior a probe shows changing. Known CHANGE explanations qualify verified regressions for REVIEW; unknown and non-CHANGE ids are ignored. Proofs record explained probe ids and their claim ids; reports show the escaped annotation. New fields default to empty for older proofs.
- Added offline coverage for the intent cases, CHANGE explanations, invalid explanation ids, prompt restrictions, report escaping, old-proof defaults, and unrelated BLOCK precedence. No real model calls or new dependencies.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (extra quiet environment setting cleared): `428 passed, 8 skipped in 18.38s`. The eight skips are Docker availability guards.
- `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`; `git diff --check` passed.
- Follow-up: Wonder probe checks now receive full intent as untrusted context for verified CHANGE quotes, retaining the strict explanation rule and quote-only behavior without intent; offline request tests added. Checks: `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (extra quiet environment setting cleared): `431 passed, 8 skipped in 15.37s`; `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`. No real model calls.
- Real check after this change (2026-10-29, Spark + Wonder + Docker, `bench/run_bench.py --only`):
  - `boundary-qty-inclusive` (faulty) is now BLOCK; in run 1 it was REVIEW, "intent is missing".
  - `price-error-type` (faulty) is BLOCK.
  - `newsletter-promo-fix` (safe) is still BLOCK. Calling Wonder directly with the same input, it agreed the probe tests its hypothesis: legacy `{"subscribed": True}` customers lose news-5. It returned `explained_by_change: null`, because the intent asks to "wire the promotion check to the new customer field" but never says legacy-flag customers lose the promotion. That is the strict anti-laundering rule working as written, so the prompt was not loosened further for this case. It stays a reported false BLOCK against its label.

### Benchmark run 2 — 2026-10-29
- `bench/run_bench.py` on main `bcd0032`: Muse Spark 1.3 + Muse Wonder 1.0, Docker, 20 candidates, 90 model calls, 0 errors. Faulty: 12 BLOCK, 0 REVIEW, 0 PASS. Safe: 1 BLOCK, 3 REVIEW, 4 PASS. The full bundles are in `bench/results/2026-10-29-run2/`; spot-checked with `proofmerge replay`.
- Compared with run 1, four faulty cases became BLOCK because of the intent-handling fix. Two became BLOCK because Spark cited C0 instead of the vague CHANGE claim it cited in run 1 (run-to-run model variation, recorded as a known limit). `newsletter-promo-fix` stays a false BLOCK under the strict CHANGE-explanation rule.
- Run 2 came after fixes made in response to run 1, so it is not a clean held-out result.

### Fresh-clone check and README redesign — 2026-10-29
- Followed the README install steps exactly, in an isolated directory on main `bcd0032`. `uv tool install git+https://github.com/imthi16/proofmerge` worked with and without `--python 3.13`. Then `proofmerge setup` built the runner image, `git clone` and `python3 demo/build_demo_repo.py` built the demo, and `proofmerge verify` on `candidate/tenant-cache-regression` gave BLOCK, exit 2, in 6 model calls. A stock Ubuntu `PATH` has no `python` command, so the README now uses `python3`.
- Final demo runs on main `bcd0032` (Spark + Wonder, Docker): tenant-cache-regression BLOCK (two runs), test-tamper BLOCK, intended-change REVIEW. `proofmerge replay` on each bundle: evidence intact.
- README restructured around a real run. The new hero image (`docs/assets/hero-{light,dark}.svg`) shows that run's base 3/3 pass and patch 3/3 fail grid. The report card screenshot is from the same run. The flowchart now shows the tamper guard and Wonder feeding the verdict, the problem table quotes the current demo intents, and the report image no longer has a dark-mode variant that pointed at the old hand-made sample.

### 2026-10-29 — T40 Replay recorded experiments
- `proofmerge replay <proof.json> --rerun --repo <repo>` verifies existing hashes first, validates full commit SHAs and their existence, then re-runs each recorded probe in Docker with its recorded attempt count and timeout. It prints per-revision outcome sequences and exits 1 on any mismatch or execution failure.
- Re-run probes and logs go to a new retained temporary directory whose path is printed; the original bundle stays unchanged. Hash-only replay remains unchanged. Empty or malformed experiment records fail before execution. No model calls or new dependencies.
- Added offline coverage for reproduction, changed base/head attempts, source preservation, pinned commits, tampered probes, missing commits, invalid records and runner failures, plus a scripted-model demo Docker integration test guarded on Docker/image availability.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (inherited extra quiet setting cleared): `462 passed, 9 skipped in 32.05s`. Docker availability guards account for the nine skips, including the new integration test. `../proofmerge-T3/.venv/bin/ruff check src tests`: `All checks passed!`. `git diff --check` passed.
- Real Docker check: `replay --rerun` on the tenant-cache demo bundle (base `9dcc104`, head `32dff53`) printed `H1 base PPP head FFF: reproduced` and `H2 base PPP head FFF: reproduced`, exit 0; the bundle's file hashes were the same before and after. README and PRIOR_ART status lines updated.

### 2026-10-30 — T39 CHANGE citation confirmation
- After a successful Wonder probe check, a verified CHANGE-cited regression qualifies for REVIEW only when Wonder confirms that a verified CHANGE claim explains it, the same test a C0-cited finding gets; otherwise it uses the implicit C0 PRESERVE rule. Missing or failed probe checks retain the previous CHANGE review behavior. Contested claims, mismatched probes and tamper rules remain unchanged.
- Added offline coverage for matching, null, non-CHANGE and other-CHANGE explanations, API/schema failures, independent claim-check failures, intent/evidence qualifications, other BLOCK precedence and the intended-change tamper path. No real model calls or new dependencies.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (inherited extra quiet setting cleared): `507 passed in 42.50s`.
- `../proofmerge-T3/.venv/bin/ruff check src tests`: failed with one existing `I001` import-formatting error at `tests/test_sandbox.py:1:1`, reproduced from its unchanged committed source. That file was left outside this change. Ruff on the four changed Python files: `All checks passed!`. `git diff --check` passed.

### 2026-10-30 — T43 Benchmark batch 3: fresh held-out set
- Added 4 faulty + 4 safe candidates, all marked `"held_out": true`, growing the bench to 16 faulty + 12 safe. New behavior lives in a base module `demo/demo-shop/src/demo_shop/cart.py` (coupon lookup, SKU listing, cart line merging, item filtering, stock release, free-shipping threshold, tenant SKU lookup) with passing base tests in `demo/demo-shop/tests/test_cart.py`.
- Faulty (BLOCK):
  - `coupon-lookup-exact`: removed case/whitespace normalization from `apply_coupon`.
  - `sku-list-order`: `list_skus` returns insertion order instead of sorted SKUs.
  - `cart-line-merge`: `merge_lines` becomes a dict comprehension that keeps the last quantity per SKU instead of summing duplicates.
  - `item-filter-stop`: `process_valid` uses `break` instead of `continue` on an invalid item, dropping all later items.
- Safe (NOT_BLOCK):
  - `extract-line-total`: rename/extract refactor — extracts `_line_total` helper in `orders.py` and uses it in `order_total` and `line_totals`.
  - `precompute-tenant-skus`: performance change — precomputes tenant SKU lists; results identical to base.
  - `intended-free-shipping-threshold`: intended rule change — raises free-shipping threshold from 100 to 150 with updated tests and an explicit CHANGE intent.
  - `release-stock-guard`: bug fix — `release_stock` now rejects negative quantities; ground truth is base FAIL / head PASS.
- Leakage rules applied: new base docstrings left unchanged on every patch except where a safe helper is introduced; intents are 1–2 sentences, ticket-free, never use the retired sentence, and none of the faulty intents states the planted behavior. Every faulty intent names the function it touches, as the safe ones do, so intent specificity does not separate the labels. The four faulty kinds are not listed in `src/proofmerge/prompts/hypotheses.md` "Where regressions hide" and were not present in the bench before. No skip/xfail markers were added.

#### Verification
- `python demo/build_demo_repo.py bench/.build/bench-shop --candidates bench/candidates --force`
- `python demo/check_demo.py bench/.build/bench-shop --ground-truth bench/ground_truth` (exit 0). All 28 branches have an existing-suite PASS; all 16 faulty candidates show ground-truth FAIL; all 12 safe candidates show ground-truth PASS.
- New-candidate ground truth on base (`main`) vs head:

```text
candidate                            label      base_gt    head_gt
coupon-lookup-exact                  BLOCK      PASS       FAIL
sku-list-order                       BLOCK      PASS       FAIL
cart-line-merge                      BLOCK      PASS       FAIL
item-filter-stop                     BLOCK      PASS       FAIL
extract-line-total                   NOT_BLOCK  PASS       PASS
precompute-tenant-skus               NOT_BLOCK  PASS       PASS
intended-free-shipping-threshold     NOT_BLOCK  FAIL       PASS
release-stock-guard                  NOT_BLOCK  FAIL       PASS
```

- `PYTEST_ADDOPTS= PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`:
  ```text
  478 passed in 41.97s
  ```
- `../proofmerge-T3/.venv/bin/ruff check src tests`:
  ```text
  All checks passed!
  ```
- `../proofmerge-T3/.venv/bin/ruff check bench/run_bench.py`:
  ```text
  All checks passed!
  ```
- The default demo check (`python demo/build_demo_repo.py demo/.build/demo-shop --force && python demo/check_demo.py demo/.build/demo-shop`) also exits 0; the added `cart.py` tests run on all demo branches and pass.
- Note: `ruff check src tests` found one pre-existing import-formatting issue in `tests/test_sandbox.py`; it was corrected with `ruff check src tests --fix` before the final check.

### 2026-10-30 — T45 Audit fixes + final demo bundles
- Copied final demo evidence bundles unchanged from `research/runs/2026-10-29-final-demo/` to `demo/runs/2026-10-29/`: `tenant-cache-take2`, `test-tamper`, `intended-change`.
- `proofmerge replay` on each bundle printed `evidence intact` (37, 33 and 16 files).
- Scanned the copied bundles for `/home/`, `/tmp/claude`, `sk-`, `ghp_`, `Bearer` and email addresses; no matches.
- README token line now cites `demo/runs/2026-10-29/tenant-cache-take2/proof.json`; added `demo/runs/2026-10-29/README.md` with verdicts, SHAs and replay command.
- `docs/PRIOR_ART.md` status line now says hash-checking replay and `replay --rerun` are built.
- Two older BUILDLOG.md commands used an absolute path to the shared virtualenv; they now use the relative `../proofmerge-T3/.venv/bin/` form used elsewhere. No author home path remains in tracked files.
- Verification:
  ```text
  $ PYTEST_ADDOPTS= PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q
  478 passed in 42.92s

  $ ../proofmerge-T3/.venv/bin/ruff check src tests
  I001 [*] Import block is un-sorted or un-formatted
    --> tests/test_sandbox.py:1:1
  Found 1 error.
  ```
- The ruff error is in `tests/test_sandbox.py`, which this task does not edit.

### 2026-10-30 — T46 Benchmark baselines
- Added an existing-suite baseline that runs the committed base `tests/` against head in the Docker sandbox, and a one-shot Muse Spark 1.3 baseline with strict decision/reason parsing, one-call budget, 8,192 completion-token limit and no retries. Candidate selection and held-out labels are loaded at runtime; evidence paths are relative to the new output directory. Model review text is never executed.
- Offline checks cover pass/fail/runner errors and timeouts, model decisions and API/schema failures without retry, usage, provenance, selection, output preservation, prompt neutrality and every held-out summary cell.
- `PYTEST_ADDOPTS= PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: `498 passed in 52.28s`.
- `../proofmerge-T3/.venv/bin/ruff check src tests bench`: `Found 84 errors.` All 84 affected files are byte-identical to HEAD (existing import-order, SIM114 and BLE001 findings). `ruff check bench/run_baselines.py tests/test_run_baselines.py`: `All checks passed!`. These unrelated files were left unchanged.
- Real Docker run, `--skip-model --out /tmp/proofmerge-bench/baseline-a-check`: 20 candidates, zero model calls, zero runner errors. Non-held-out faulty: 1 BLOCK / 7 NOT_BLOCK; held-out faulty: 0 BLOCK / 4 NOT_BLOCK. Non-held-out safe: 2 BLOCK / 3 NOT_BLOCK; held-out safe: 0 BLOCK / 3 NOT_BLOCK. The safe blocks are intended-discount-rate and intended-express-fee: original tests reject their intended changes. Output is retained outside the repository. No real model review was run.

### 2026-10-30 — Benchmark run 3 and baselines
- [Run 3 results](bench/results/2026-10-30-run3/results.json), git SHA `5df3735d1de66590e00859b21af858eb5baaa3eb`: faulty 14 BLOCK / 2 REVIEW / 0 PASS / 0 ERROR; safe 1 BLOCK / 6 REVIEW / 5 PASS / 0 ERROR. Held out: faulty 6 / 2 / 0 / 0 and safe 0 / 4 / 3 / 0 in the same order. 121 model calls. Rules were frozen before batch 3 was written; the held-out slice is the clean number.
- [Baseline results](bench/results/2026-10-30-baselines/results.json), same candidates: base tests on head (A) blocked 1 of 16 faulty and 3 of 12 safe; one-shot Spark (B) blocked 16 of 16 faulty and 0 of 12 safe, with 28 model calls. B scored every candidate correctly. The small synthetic bench and function-naming intents do not separate the approaches on accuracy.
- ProofMerge's distinction is evidence: a BLOCK is backed by a Spark-written probe passing base 3/3 and failing head 3/3, recorded and replayable with `proofmerge replay`; B gives an unverifiable opinion with no repro. PASS means no regression found within tested scope. Details and disagreements: [benchmark results](bench/results/README.md).

### 2026-10-30 — Report layout refresh
- `report.html` now leads with the evidence: verdict banner, then hypothesis cards, then the tamper guard, intent claims, provenance and scope. Each card's grid puts Spark's expected base and head behavior next to the three outcomes per revision, as filled PASS/FAIL cells; on narrow screens each revision stacks its expectation above one row of attempts. Claims are one flat list with CHANGE/PRESERVE tags, provenance is a label/value list, and a run with no hypotheses says so. Copy is unchanged; the page is still static, with no scripts or external resources.
- Checked by rendering the three committed demo proofs at 1280 px, 390 px and in dark mode. The committed demo and run 3 bundles keep the report they were recorded with, because `manifest.json` hashes `report.html`.
- `PYTEST_ADDOPTS= PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: `527 passed`. `ruff check src tests`: all checks passed.

### 2026-10-30 — T48 Agent Skill
- Added `skills/proofmerge/SKILL.md`, an Agent Skill that tells a coding agent to commit its change, write the intent before it sees a verdict, run `proofmerge verify`, and report PASS / REVIEW / BLOCK as ProofMerge states it. It forbids editing tests, probes, intent or evidence to change a verdict, and caps fix attempts at two before handing back to the user.
- `tests/test_skill.py` checks the frontmatter, that every `proofmerge` command and option the skill names exists in the CLI, and that its call budget (19) matches `verify`.
- README: install line for Claude Code and a "What works today" row. Not yet used by an agent on a real task.
- `PYTEST_ADDOPTS= PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: `530 passed`. `ruff check src tests`: all checks passed.

### 2026-10-30 — T49 Model backends: other endpoints and plans
- `meta.py` now maps the Spark and Wonder roles to models chosen with `PROOFMERGE_SPARK_MODEL` / `PROOFMERGE_WONDER_MODEL`, and `PROOFMERGE_BASE_URL` selects any OpenAI-compatible endpoint. Defaults are unchanged: Meta API, Muse Spark 1.3, Muse Wonder 1.0. `*-contributor` models stay refused unless `PROOFMERGE_ALLOW_CONTRIBUTOR=1`.
- `cli_backend.py` adds `PROOFMERGE_BACKEND=claude-cli`: each call runs `claude -p` with no tools, no MCP servers and no settings files, in a fresh empty directory with a minimal environment, so a Claude plan can be used without an API key.
- `proof.json` records the endpoint, the model per role and whether contributor models were allowed (`model_usage`); the report shows them under Provenance. Older proofs still load.
- Canary check, 2026-10-30, an empty directory holding `canary.txt` and a prompt asking the CLI to read it with any tool: Claude Code 2.1.314 with `--tools ""` replied that it had no file tool; Grok CLI read the file with `--tools ""` under both `--permission-mode default` and `dontAsk`, so it is refused. Codex has no option that removes its shell tool. Other CLIs were not checked. Details: `docs/model-backends.md`.
- Real call: `PROOFMERGE_BACKEND=claude-cli PROOFMERGE_SPARK_MODEL=haiku proofmerge smoke` returned `ok=True`, 1128 prompt and 81 completion tokens. No full `verify` has run on a non-default backend, and the OpenCode Go endpoint has not been called.

### 2026-10-30 — T50 Dashboard
- `proofmerge dashboard <folder>` serves a React page on 127.0.0.1 that lists every `proof.json` under the folder. Runs view: a verdict bar that doubles as the filter, search over run ids and reasons, an evidence-integrity filter (`verify_manifest` on each run), a "disagrees with label" filter when a benchmark `results.json` sits next to the runs, sortable columns, arrow-key navigation, and a detail panel with reasons, SHAs, model usage, each hypothesis's base/head grid, why head failed, probe source, tamper findings, intent claims, the replay command and a link to the full report. Benchmarks view: label-by-decision counts for each run_bench `results.json`, a held-out toggle, the A/B baselines when they cover the same candidates, and the cases that do not match their label.
- The server answers GET only, binds to 127.0.0.1, looks run ids up in the latest scan instead of joining request paths, skips `proof.json` symlinks, and sends a `default-src 'self'` Content-Security-Policy. Evidence text is rendered as React text, never as HTML. React 18.3.1, React DOM 18.3.1 and htm 3.1.1 are vendored with their licenses; no build step and no CDN.
- `tests/test_dashboard.py`: 8 tests, covering scan, integrity after an edited probe, unreadable proofs, labels, benchmark parsing, run detail, path traversal, POST refusal and the security header. A wheel built with `uv build` contains the static files.
- Checked in headless Chrome against `bench/results` (48 runs, 4 results files) at 1440 px, 390 px and in dark mode. Fixed after looking: a "recorded" column that showed copy times, and the page scrolling to the selected run on load.
- Pull requests tab (`--repo`): `gh pr list` (50 most recent, cached 60 s) with each PR's verdict when a run in the folder has the same head SHA. "Verify this PR" (open PRs only) asks for confirmation, then fetches `+refs/pull/N/head` and the base branch into `refs/proofmerge/` and runs `python -m proofmerge.cli verify` with the PR title and body as the intent, writing a new run into the folder; one job at a time, polled every 2 s. The POST needs the per-server token from `/api/session`, `Content-Type: application/json` and a same-origin `Origin`, and every request must carry this server's `Host` (DNS-rebinding guard). Six more tests with fake `gh`/`git`/`verify`: head-SHA matching, token/origin/content-type refusal, the exact fetch refspecs and verify arguments, one job at a time, closed PRs refused, foreign Host refused. The list was read for real from this repository (38 PRs); no PR was verified from the page.
- Usability pass, checked by clicking through in Chrome on `bench/results`: each base/head result in a hypothesis grid is a button that shows that attempt's command, exit code, duration, stdout and stderr (last 6,000 characters each); a run-set filter when runs sit in sub-folders; Export CSV of the filtered runs; a copy-link button for a run; benchmark mismatches open their run; PRs list runs from earlier commits (runs started from the page are named `pr-<n>-<sha>-<time>`); a "new runs" banner from a 15 s poll of the cheap `/api/ids` list while the page is visible; the URL hash is followed (back/forward); the run list scrolls on its own on wide screens and keeps the selected run in view. Grid buttons are labelled with their outcome for screen readers. Two more tests (execution logs in the run detail, `/api/ids`, earlier-commit PR runs). `pytest -q`: 546 passed; `ruff check src tests`: all checks passed.
