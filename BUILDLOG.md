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

### 2026-10-30 — T51 Scope receipt
- `report.scope_receipt()` turns a proof into plain sentences about what the run checked: how many hypotheses Spark proposed, how many probes ran on base and head and with how many attempts, how many passed on base every time, which hypotheses had no probe, and how many test files changed by the PR were re-run from base on head (one tamper experiment per file, not one per tamper finding). When no hypothesis probe ran but changed tests did, it says so instead of claiming nothing was tested. A PASS adds "PASS covers only these checks". `report.html` shows it under "What this run checked", right after the verdict, and `proofmerge verify` prints it as a `checked:` line. Verdict rules are unchanged.
- On the run 3 bundles, the receipt makes it plain that `pure-refactor-constants` is a PASS with 0 hypotheses and no probe.
- Tests: receipt text for a run with unprobed hypotheses, for a PASS with no probe, its place in the report, and the `checked:` line in `verify` output. `pytest -q`: 567 passed. `ruff check src tests`: all checks passed.

### 2026-10-30 — T52 Verdict rule and positioning docs
- README: a collapsible note on the one verdict rule that a reviewer flagged as looking like an exception to "a model only moves toward REVIEW": a verified regression citing a CHANGE claim is REVIEW when Wonder did not run or confirmed an explaining CHANGE claim, and is judged against C0 (so it can be BLOCK) when Wonder ran and found none. The note explains why the BLOCK still rests on code-decided runs and why the rule exists. No code change.
- docs/PRIOR_ART.md: "Commercial reviewers that run code", with CodeRabbit and Greptile TREX cited from their own pages (pages fetched on 2026-10-30; tools not tried), and the narrower statement of what ProofMerge adds.

### 2026-10-30 — T53 Config-driven multi-language support (Go demo)
- Added `src/proofmerge/project_config.py`: pydantic `ProjectConfig` validates `.proofmerge.toml` fields strictly, and `load_config(repo, base_sha)` reads the config from the base commit so a PR cannot rewrite how it is tested.
- Added a config runner in `src/proofmerge/sandbox.py`: exports the base/head tree, writes the probe at the configured `probe_path`, mounts the copy read-only at `/work`, and runs `test_command` in the user-provided image with the same isolation flags as the Python runner. Outcome mapping: pass exit codes → PASS, timeout → TIMEOUT, runner failures → ERROR, other non-zero + regex match → FAIL, other non-zero → ERROR. Records `command_argv` and the image digest.
- Added generic prompts `src/proofmerge/prompts/probe_generic.md` and `src/proofmerge/prompts/hypotheses_generic.md`, parameterized with `{language}`, `{probe_path}`, `{test_command}`, `{assertion_failure}`, keeping the untrusted-data rule verbatim.
- `src/proofmerge/probes.py` and `src/proofmerge/hypotheses.py` use the generic prompts and config runner when a config exists; skip the Python-only `private_names` check for non-Python languages.
- `src/proofmerge/pipeline.py` loads config once after reading context, passes it through, skips the Python-only tamper guard for config languages, and adds a scope limitation. `Proof` gains a `language` field (default `python`); `report.html` shows it when not Python.
- Added `demo/build_go_demo.py` and `demo/candidates-go/go-tenant-cache/intent.md`, building a Go repo with a package-level SKU cache that makes an unknown tenant return a cached price instead of an error.
- Docker check (no model call): `docker pull golang:1.23-alpine`, built demo to `/tmp/proofmerge-go-demo`, and ran the config runner directly with a hand-written probe. Outcomes:
  - base PASS 3/3, head FAIL 3/3 (unknown tenant after cache prime must error).
  - Compile-error probe returned ERROR, not FAIL.
- Tests: parsing/validation, load from base not head, config docker argv, outcome mapping table, generic prompt placeholders, pipeline scope limitation and tamper skip, Go demo builder, unchanged Python prompts and argv. `PYTEST_ADDOPTS= PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q`: `605 passed`. `ruff check src tests demo/build_go_demo.py`: `All checks passed!` (older demo scripts have pre-existing lint findings and were left unchanged).
- Docs: README "What works today" row, `docs/languages.md` with fields and limitations.
- Review fixes: the first Go demo broke its own suite on the candidate branch (`TestPriceForUnknownTenant` ran after a price was cached), so the existing tests caught the bug. The unknown-tenant test now runs first; checked in Docker that `go test ./pricing` passes on both main and the candidate. The generic probe prompt now says the test must be named so `test_command` selects it, because a test that `go test -run` does not select never runs and reads as PASS. Re-ran the runner check after the fix: probe base PASS 3/3, head FAIL 3/3; compile-error probe ERROR 3/3 on both.
- Limitation: in Go, a panic inside the probe also prints `--- FAIL: TestProofMergeProbe`, so it counts as FAIL, not ERROR as in the Python runner. The `runner` argument is not used on the config path (it always runs Docker), so the config path is tested offline only through fakes of `run_config_experiment`.
- `replay --rerun` now uses the config runner for a proof whose `language` is not Python, with `.proofmerge.toml` read from the recorded base commit (it used to run every probe with pytest). Two offline tests added.
- Real run (5 Meta calls: 4438 prompt, 7449 completion tokens; 5 min 35 s): `proofmerge verify /tmp/proofmerge-go-demo --base main --head candidate/go-tenant-cache --intent-file demo/candidates-go/go-tenant-cache/intent.md` gave `verdict: BLOCK`, "H1 is a verified regression against uncontested PRESERVE claim C3 with intent present." Spark proposed one hypothesis and wrote a Go probe that primes the cache with tenant-a and expects an error for an unknown tenant: base PASS 3/3, head FAIL 3/3 (`second call with unknown tenant succeeded with price 25`). Scope limitations include the missing tamper guard. `replay` gave `evidence intact: 18 files`; `replay --rerun --repo /tmp/proofmerge-go-demo` gave `H1 base PPP head FFF: reproduced`. Bundle committed in `demo/runs/2026-10-30-go/go-tenant-cache`; a fresh build of the demo gives the same base `8aae692` and head `672f3f5`.

### 2026-10-31 — T54 Report screenshots in the current design
- `docs/assets/report-full-real-light.png` and `report-card-real-light.png` were still the old report look. Re-rendered `report.html` from the committed `demo/runs/2026-10-29/tenant-cache-take2/proof.json` with the current `render_html` (the committed bundle is unchanged), then took headless Chrome screenshots at 1280 px width, light mode. The card image shows only the H1 card (other elements hidden with CSS on a scratch copy), trimmed to the card. Same run, same content as before; README alt text still matches.

### 2026-10-31 — Python monorepo import paths and optional hashed installs
- A `[python]` table in the base commit's `.proofmerge.toml` sets repository-relative import roots for the existing Python pytest runner. The default `PYTHONPATH=/work/code/src:/work/code` is unchanged when the table is absent or sets neither `root` nor `pythonpath`. The head commit's file is not read. Absolute paths, `..`, and symlinks are rejected. `--config` is an operator path; the GitHub Action does not pass it, and a checkout file that differs from the base blob is refused.
- `install = false` by default. `install = true` builds hashed `name==version` requirements from the base commit's `uv.lock` (wheels only, registry only) or a hashed requirements file, installs them in a separate container with network and no host environment, then runs probes with `--network none`. Head lockfile changes are not installed. Import and install failures are `setup_failure` / classification `ENVIRONMENT` and verdict REVIEW, never PASS or BLOCK.
- `fail-on-missing-key` defaults to false. A missing `MODEL_API_KEY` still skips verification and stays green, and now writes `skipped.json` plus a warning. `true` fails the job after that record. It is not a code verdict.
- Checks: `ruff check src tests` → `All checks passed!`. `PYTEST_ADDOPTS= /tmp/pm-venv/bin/python -m pytest -q` → `635 passed, 9 skipped in 24.69s`. The nine skips are `docker or runner image missing` (`docker` is not installed here). `git diff --check` passed.
- HelpDeck base `e7b5e7af93a4c410cc6595e13b2a30a8dd2fe81c` `uv.lock` plus `pyproject.toml` planned 118 hashed packages from `https://pypi.org/simple` (the project itself was not included). They were not installed.
- HelpDeck smoke, not a verdict: Docker was unavailable, so `host_python_runner` applied the same import-path function outside a container. Operator file `/tmp/helpdeck-proofmerge.toml` (`root = "apps/api"`, `install = false`), not the checkout. `from app.agent.prompts import format_context` then `format_context([{"content": "Returns are accepted."}])`: import probe base PASS, head PASS. A second probe that asserts the base string: base PASS (`[1] Returns are accepted.`), head FAIL with `AssertionError: assert '[1] returns are accepted.' == '[1] Returns are accepted.'`. That difference is the intentional lowercasing. It is not a BLOCK.
- Review fix (Codex P1 on #46): on a non-root host the installer container wrote `/deps` as UID 65534, so the host's `open_to_runner()` chmod failed (`environment setup failed: [Errno 1] Operation not permitted: .../deps/greetlib`) and every `install = true` run became a setup failure. Deleting the temporary directory then also failed (`PermissionError` on `greetlib-1.0.0.dist-info/RECORD`). Reproduced both with real Docker as a non-root user. Fix: the installer container runs as the host's UID:GID when that is not root, else as `65534:65534` (`sandbox.installer_user()`); never as root. New Docker test `test_docker_install_needs_no_host_chmod_and_cleans_up` runs the real installer container against a local wheel index (only `requirements_for_base` and a `--trusted-host` for that local http index are faked), then a real probe imports the package from `/deps`. It failed before the fix as non-root and passes after, as root and as non-root.
- `poetry.lock` and `Pipfile.lock` installs (`src/proofmerge/lockfiles.py`), so `install = true` covers uv, Poetry, Pipenv and hashed `requirements.txt` projects, not one repository's setup. Same rules as `uv.lock`: base commit only, read as data, one https index, sha256 hashes, markers for the Linux runner, path/git/URL packages refused. Poetry starts from the main dependencies in the base `pyproject.toml` and follows extras; Pipfile.lock installs `default` only.
- Checked against real lockfiles: Poetry 2.5.1 and Pipenv 2026.10.4 each locked the same `requests[socks]` + `attrs` + Windows-only `colorama` + dev `pytest` project. Both planners produced the same 7 packages (attrs, certifi, charset-normalizer, idna, pysocks, requests, urllib3; no colorama, no pytest), and `pip install --require-hashes --only-binary=:all: --no-deps --target` from https://pypi.org/simple installed each set and imported `requests`, `socks` and `attrs`. Those lockfiles were scratch files, not committed; `tests/test_lockfiles.py` uses small fixtures in the same shape (25 tests).

### 2026-11-01 — T55 LangGraph orchestrator, CI, review rules
- `pipeline.run_verify` split into twelve stage functions over one `VerifyState`. The default `legacy` orchestrator calls them in order; `graph.py` runs the same functions as a LangGraph `StateGraph` (`--orchestrator langgraph` / `PROOFMERGE_ORCHESTRATOR`, extra `proofmerge[graph]`, LangGraph 1.2.15). The verdict still comes only from `verdict.decide()`.
- `MetaError.transient` marks HTTP 429/5xx and connection failures. Only the four model-only nodes retry them (at most two retries, same call budget); the probe and tamper nodes never retry. `proof.json` gains an optional `orchestration` record (engine, nodes, retries) that the verdict policy does not read.
- `tests/test_graph.py`: nine PASS/REVIEW/BLOCK scenarios give the same `proof.json` (apart from `orchestration`) and byte-identical evidence files on both orchestrators; retry recovery, exhaustion, no retry for non-transient errors, sandbox runs not repeated, `decide()` called once. `tests/test_pipeline.py` also passes in full with `PROOFMERGE_ORCHESTRATOR=langgraph` (71 passed, 1 Docker skip).
- Real Docker in this session (runner image built locally with the proxy CA added for the build only): `PROOFMERGE_REQUIRE_DOCKER=1 pytest tests/test_sandbox.py tests/test_pipeline.py tests/test_replay_rerun.py` gave 164 passed, 0 skipped; the real-Docker demo run gave BLOCK on the LangGraph orchestrator too.
- Added `.github/workflows/ci.yml` (lint + unit on Python 3.11 and 3.13, LangGraph parity, Docker integration that fails instead of skipping), the `orchestrator` input in `action.yml`, `AGENTS.md` review rules for Codex, and `docs/orchestration.md`. The CI workflow has not run on GitHub yet.
- Not built: checkpoint/resume (needs idempotent experiment keys) and OpenTelemetry spans.

### 2026-11-01 — T56 Control Center (typed API, live progress, new UI)
- Stage progress: `pipeline.Deps.observer` reports started / completed / retrying / skipped with counts and durations; the decision only after `deterministic_decide`, as `verdict.decide()` returned it. Legacy loop and LangGraph nodes/routers report the same sequence. Observer errors are swallowed. `verify --events-file` (hidden, refused inside the evidence dir) writes it as JSON lines. `run_verify` is called exactly as before when no observer is active.
- Dashboard: typed `/api/v1` (`dashboard_models.py`, `dashboard_api.py`) beside the unchanged `/api/*`; per-job bounded `EventJournal` fed by a validating tailer; SSE at `/api/v1/jobs/{id}/events` with `Last-Event-ID` replay, heartbeats and at most 8 streams; strict `POST /api/v1/verifications` with the same Host/Origin/token checks; diagnostics that report presence only; stat-keyed proof and integrity caches. A plain function stored as a handler attribute became a bound method (caught by the diagnostics test, fixed with `staticmethod`).
- Opt-in OpenTelemetry (`PROOFMERGE_OTEL=1`, extra `otel`) with an attribute allowlist.
- UI: `ui/` (React 19, TypeScript 5.9, Vite 8), built into `dashboard_static/app/` and committed; previous page moved to `/legacy/`. Routes: overview, pull requests, live verification, runs, evidence explorer, benchmarks, diagnostics. Types generated from the Pydantic models.
- Tests: Python `ruff` clean; `pytest` 687 passed, 9 skipped (Docker not available in this session); LangGraph env run of pipeline/graph/progress tests 129 passed, 1 skipped. New: 51 Python tests (v1 API, SSE replay/disconnect/limits, event validation, observer no-effect on evidence for both engines, legacy/graph event parity across all nine scenarios, sentinel secret absent from events and spans, schema/build drift). UI: eslint and tsc clean, Vitest 18 passed, Playwright 14 passed (desktop and Pixel 7) against the real Python server with fake `gh`/`git`/`verify`. A clean rebuild reproduced the committed bundle byte for byte.
- Checked in headless Chromium at 1400 px and 390 px, light and dark: no console errors or CSP violations, no horizontal scroll after fixing the header nav.
- Not done: the new CI `ui` job and the Docker job have not run on GitHub for this branch yet; no real model or Docker run was made through the new UI. Probe attempts are not streamed individually (stage granularity only).

### 2026-11-01 — T57 Sandbox isolation in one place; verdict recomputation in replay
- Sandbox: the isolation flags were written out separately for the pytest and the config-language containers, as were the capped-output/timeout loop and the revision/attempt loop. `hardened_run_argv()` is now the one prefix every probe container starts with; `_run_capped()` and `_run_revisions()` are shared. Container argv is byte-identical to before (snapshot of both argv builders, with and without a job label, compared before and after). `findings.classify` and the tamper guard used a literal 3; they use `sandbox.ATTEMPTS`. New test: each isolation flag appears exactly once, no loosening flag, every mount read-only, no Docker socket.
- Replay: `proof.json` now records `verdict_inputs` (stage errors, intent established/given, Wonder checked, tamper-guard language), the policy inputs that were not already in the proof. `verdict.adjudicate()` is the one function `verify` and `replay` both use. `proofmerge replay` reclassifies every execution, checks pytest outcomes against exit codes, reruns the verdict policy, and fails when the recorded findings or verdict differ, even after the manifest is rewritten. The dashboard marks such a run `changed`. Proofs written before this have no `verdict_inputs`; replay prints that it did not recompute them. Limit, pinned by a test: a consistent rewrite of the records themselves passes; `--rerun` or signatures are needed for that.
- Checks: ruff clean; full pytest with Docker (`PROOFMERGE_REQUIRE_DOCKER=1`): 659 passed, 0 skipped; `test_pipeline.py` + `test_rederive.py` on the langgraph orchestrator: 82 passed. Every legacy/LangGraph parity scenario recomputes to the identical verdict; `tests/test_rederive.py` (edited decision, outcome, reason, unknown claim, old proof, dashboard). Three `test_replay_rerun.py` tests (10 cases) that edit execution records to exercise the `--rerun` record checks now drop `verdict_inputs` first (as in an older proof), because recomputation stops those edits earlier; a new test covers that.
- Review fixes (Codex on #48): tamper findings now record the `T` experiment their outcome came from (`experiment_id`), the outcome rule is one function (`tamper.head_outcome`), and replay recomputes each tamper PASS/FAIL from that experiment's executions, so an invented tamper FAIL no longer replays. The pytest exit-code check now runs only for the pytest runner, not for a `.proofmerge.toml` runner whose language is Python. Both new tests failed on the previous `rederive.py`.
### 2026-11-02 — T57 Browser app: every command without a terminal
- `proofmerge app` (and a `proofmerge-app` launcher script, a double-clickable `.exe` on Windows) opens the dashboard in the browser with runs saved in `~/ProofMerge/runs`; if port 8765 is busy it takes a free one. A Quit button stops the server, since there may be no terminal to press Ctrl+C in.
- `src/proofmerge/dashboard_jobs.py` runs any CLI command from the page as `python -m proofmerge.cli ...`: an argument list, never a shell, one job at a time, output streamed to the page, Stop kills the process and its sandbox containers by `PROOFMERGE_JOB_ID` label. Inputs are checked before git sees them: the project must be a Git work tree, refs must not look like options or ranges and must resolve to commits, and the server names each run folder. Folder listing, branch listing, health and job output need the session token, like every POST. A model key typed into Setup stays in server memory and the job environment only; the page only learns whether one is set.
- Page: a sidebar app with Home (pick a folder, "Check *change* before it goes into *base*", the PR description, and three modes: full check, risks only, tests without a verdict), Runs (now with Check evidence, Run the tests again and Download report), Pull requests (follows the chosen project), Benchmarks and Setup (Git, Docker, sandbox image build, model key and connection test, GitHub CLI). A running check shows five steps that light up from the files the run has written; a finished verify ticks only steps whose files exist and marks the rest "Did not run". Geist and Geist Mono (OFL) are vendored; the dashboard and report share one dark-first palette.
- `report.html` inlines the two fonts as data URIs, so it stays one file with no requests; the dashboard's report CSP gains `font-src data:`.
- The missing-key error now names the Setup page as well as the shell export.
- Tests: `tests/test_dashboard_jobs.py` (project and ref checks, branch guess, folder listing, verify argv and run naming, key kept out of settings, one job at a time, stop, token on folder listing and jobs, step flags) and a report font test. `pytest -q`: 624 passed. `ruff check src tests`: all checks passed. Impeccable detector: no findings (Geist flagged as overused and kept on purpose).
- Checked in headless Chrome driven over CDP at 1440 px and 390 px: Setup key save, folder picker, branch pick, start, running steps, the REVIEW reveal and Runs. A real `verify` on the demo shop ran from the page with `PROOFMERGE_BASE_URL` pointed at a closed local port: REVIEW "could not reach the Meta API", only the read and verdict steps ticked.
- Not done: a run from the page with a real model key, any run on Windows, and the README report screenshots, which still show the previous report look.

### 2026-11-02 — T57 PR #50 review fixes (Codex)
- PR verification now receives the in-memory Setup key through the same environment builder as Home jobs. It is not stored in settings or job output.
- Every Home action, including replay --rerun, has a unique sandbox job label so Stop/timeout can clean up its containers. POST actions share one lock, and changing project while a PR runs is refused, preserving the one-action limit.
- The composer lets the CLI validate the configured runner image instead of requiring the Python image for every language. Job polling retries transient failures and cancels its timer on cleanup.
- Offline regression coverage includes key forwarding, rerun cleanup, project switching, concurrent PR/Home starts, and browser polling/form validation executed with Node. No model/network calls in tests.
- Validation: `ruff check src tests`: `All checks passed!`; `PYTEST_ADDOPTS= PYTHONPATH=src python -m pytest -q`: `630 passed in 94.73s (0:01:34)`; `node --check src/proofmerge/dashboard_static/app.js` and `git diff --check`: exit 0. `graphify update .` completed (AST only).

### 2026-11-02 — PR #50 conflict resolution against current main (Codex)
- Merged main e5e4be5 while preserving the Control Center at `/`, all v1 API/SSE/cache functionality, the browser app at `/app/` (also `/legacy/`), and the existing review fixes. `proofmerge app` opens `/app/`. Font assets, report embedding and CSP use the merged legacy paths. Both verification APIs share the action lock and refuse overlap with Home jobs. Core pipeline, sandbox and verdict files match main.
- New regressions cover separate UI entry points, font serving, and the shared Home-job guard on both PR APIs.
- Validation: `ruff check src tests`: `All checks passed!`; full `pytest -q`: `801 passed, 1 skipped in 137.22s (0:02:17)`; LangGraph pipeline/graph/progress suite: `129 passed, 1 skipped in 27.35s`. The skip is the optional OpenTelemetry test (dependency unavailable), not Docker. Tests use temporary pinned LangGraph/Pydantic dependencies and offline fakes.
- UI: lint, typecheck, unit tests and build passed; generated API schema matches; committed Control Center bundle reproduced without changes; Playwright desktop/phone: `16 passed (45.7s)`. Browser navigation produced harmless BrokenPipeError messages when clients disconnected. `node --check` for the browser app and `git diff --check` passed. `graphify update .` completed (AST only).

### 2026-11-02 — T58 Windows installer
- `installer/windows/Install-ProofMerge.cmd`: one file to download and double-click. A batch header hands the file to Windows PowerShell 5.1 (`-ExecutionPolicy Bypass` for that process only), which: finds Python 3.11+ through the `py` launcher or offers `winget install Python.Python.3.13`; finds Git or offers winget; makes a venv in `%LOCALAPPDATA%\ProofMerge\venv` and pip-installs `proofmerge @ git+https://github.com/imthi16/proofmerge@main` (`PROOFMERGE_REF` picks another ref); offers Docker Desktop through winget; writes Start menu and desktop shortcuts to `proofmerge-app.exe` plus an Uninstall shortcut; opens the app. PATH and the system are not changed; no admin rights are needed except what winget asks for. Winget shows each license and the user accepts it; the script passes no accept flags and downloads no scripts to execute. Running it again updates ProofMerge. The uninstaller removes the venv and shortcuts and keeps `%USERPROFILE%\ProofMerge`.
- Python is found by parsing `--version`, because Windows PowerShell 5.1 drops inner double quotes when it calls a program and the Store `python` stub prints nothing useful.
- `.gitattributes` keeps `*.cmd` at CRLF; cmd mis-reads LF batch files.
- Tests: `tests/test_windows_installer.py` (single file, CRLF and ASCII, the self-path hand-off, per-user install target and launcher, no accept flags or remote script execution, three winget installs behind three questions, uninstaller keeps the runs). `pytest -q`: 628 passed. `ruff check src tests`: all checks passed. Not run on Windows: no Windows machine or PowerShell here.
- Review fixes after a static review on Linux (the reviewer had no Windows machine either): the raw GitHub download was LF because `eol=crlf` only converts on checkout, so `.gitattributes` now stores `*.cmd -text` and the blob itself is CRLF. A re-run now stops the app (it locks `proofmerge-app.exe`) and uninstalls ProofMerge before installing, because pip keeps an installed `0.1.0` even with `--upgrade` when only the code changed; pip output is no longer quiet. `powershell.exe` and `taskkill.exe` are called by full path, since cmd searches the current folder (often Downloads) first. `PROOFMERGE_REF` must look like a branch, tag or commit. Python and Git are looked up again after winget instead of trusting its exit code. The uninstaller moves to `%TEMP%` (the shortcut no longer starts it inside the folder it deletes), stops the app, and looks up the Start menu and desktop folders when it runs, so a non-ASCII desktop path is not garbled. Windows PowerShell 5.1 turns redirected stderr into a terminating error under `Stop`, so the two calls that may print "not found" run with `Continue`. `pytest -q`: 632 passed. `ruff check src tests`: all checks passed. Still not run on Windows.
- Second static review (still no Windows): uninstalling before installing left no app when the download failed, so a re-run now forces a reinstall of ProofMerge only (pip replaces the old copy after the new one builds) and then installs any new dependencies; checked with pip on Linux that a bad ref keeps `proofmerge-app` and a good one moves `direct_url.json` from 8686e1e to 6436ea5. The ref check uses `\A…\z`, since `$` lets a trailing newline through. The installer and uninstaller stop only processes started from this install (by path) and wait for them to exit, instead of `taskkill /im`, which stopped every `proofmerge-app.exe`. The app the installer opens runs in `%TEMP%`, like the shortcuts.

### 2026-11-02 — T58 PR #51 winget review fix (Codex)
- Forward winget output to Out-Host before capturing LASTEXITCODE. Package information and diagnostics are visible, while Install-WithWinget returns only its Boolean exit status. Docker installation failures no longer become truthy because native output entered the return stream.
- Verified the existing force-reinstall/no-deps flow and its regression check for updates without a version bump. Added a check for output forwarding before exit-code capture. The installer keeps ASCII/CRLF.
- Validation: `ruff check src tests`: `All checks passed!`; `PYTEST_ADDOPTS= PYTHONPATH=src python -m pytest -q`: `634 passed in 92.99s (0:01:32)`; installer-specific suite: 10 passed; `git -c core.whitespace=cr-at-eol diff --check`: exit 0. `graphify update .` completed (AST only).
- Limitation: Windows/PowerShell execution remains unverified on this Linux machine.

### 2026-11-02 — PR #51 stacked conflict resolution (Codex)
- Merged resolved T57 base 2529ffa into the installer branch. README keeps both the current app/Control Center routing and the Windows installer row; BUILDLOG keeps both histories. Installer force-reinstall, Out-Host status handling, CRLF and safety checks are preserved. Product/UI code exactly matches the independently tested T57 base.
- Validation: `ruff check src tests`: `All checks passed!`; full `pytest -q`: `811 passed, 1 skipped in 123.62s (0:02:03)`. Skip: optional OpenTelemetry dependency unavailable. The suite includes all ten installer static checks; Windows execution remains unverified. `node --check`, CRLF-aware `git diff --check`, and `graphify update .` passed.
- Base PR #50 GitHub CI is green in all five jobs (Python 3.11/3.13, Docker, LangGraph parity, Control Center). The UI tree is identical to that base, whose desktop/phone Playwright suite passed all 16 cases.

### 2026-11-02 — T59 GitHub sign-in, repository and pull request picker (Claude)
- `proofmerge app` now signs in to GitHub without a terminal: Setup runs `gh auth login --web` with no TTY, so gh prints a one-time code and waits; the page shows the code, a Copy button and an Open GitHub button that always points at https://github.com/login/device (never at a URL from gh's output). gh keeps the token in the system credential store; ProofMerge never reads, logs or stores it. Sign-in can be cancelled and expires with the 15-minute code.
- Pull requests lists the signed-in user's repositories (`gh api user/repos`, own, collaborator and organization repositories) or takes any `owner/name` or GitHub URL, clones it with `gh repo clone` into `~/ProofMerge/repos/<owner>/<name>` (later picks only fetch), makes it the project through the existing `/api/settings`, and shows its pull requests. Cloning never runs the repository's code. Names are checked against `owner/name` before any command; an existing non-Git folder is never overwritten.
- `git fetch` of a pull request adds `-c credential.https://github.com.helper=!gh auth git-credential` for that one command, so private repositories work with the gh sign-in without changing the user's git config.
- A network failure is reported as "Could not reach GitHub", not as signed out: gh exits 4 when nobody is signed in and a revoked token answers HTTP 401; anything else is unreachable.
- New routes (all behind the session token and the existing Host/Origin checks): GET `/api/github`, `/api/github/repos`; POST `/api/github/login`, `/api/github/login/cancel`, `/api/github/logout`, `/api/github/clone`. The Windows installer offers the GitHub CLI (`GitHub.cli`, optional, behind a question) as step 3 of 7.
- PR table: long branch names wrap instead of squeezing the title to one letter per line.
- Checked for real on Linux: gh 2.46 sign-in started with an isolated `GH_CONFIG_DIR` printed a code, and Cancel stopped it; with the imthi16 account the page listed 24 repositories, cloned imthi16/proofmerge, fetched it on a second pick and listed its 50 pull requests. Screenshots at 1440 px and 375 px. A full sign-in by a person and any Windows run are not done yet.
- Tests: `tests/test_dashboard_github.py` (repo names, sign-in states, cancel, generic failure text, offline vs signed out, repo list, clone then fetch, no overwrite, HTTP token checks), installer step order. `pytest -q`: 833 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-02 — T60 README redesign (Claude)
- README rebuilt around the current product, every fact, number and limit kept: a new hero (the H1 evidence ledger from the real tenant-cache-take2 run: base 9dcc104 pass 3/3, head 32dff53 fail 3/3, BLOCK) drawn in the app's Geist type and palette, dark and light PNGs with transparent corners; a "Use it without a terminal" section with real app screenshots (Runs page on the committed demo runs, the GitHub sign-in card); the H1 report card and the whole report re-captured in the current report design (rendered with `proofmerge report` to a scratch folder; the committed bundles were not touched); drawn pass/fail/review icons instead of emoji; the mermaid diagram recoloured to the app palette; long install options folded into details; "What works today" gains the GitHub sign-in row; the repository layout lists dashboard_jobs.py, dashboard_github.py and installer/windows.
- Replaced assets removed: hero-dark.svg, hero-light.svg, report-card-real-light.png, report-full-real-light.png. Checked by rendering the README through GitHub's markdown API with github-markdown-css in headless Chrome; every `docs/assets` path in the README exists.

### 2026-11-02 — T61 Agent Skill rewrite and Claude Code / Codex plugins (Claude)
- `skills/proofmerge/SKILL.md` rewritten: the trigger description says when to use it and when not (uncommitted edits, untrusted repositories, reviews without a run); the hard rules (never edit tests, probes or evidence; never claim an unrun verdict; never ask for the key; ask before a third run or an untrusted repository) come before the steps; a preflight checks `proofmerge version`, the key (without printing it) and Docker; the intent is written outside the repository before any verdict; a verdict table says what to do on BLOCK, REVIEW, PASS and exit 1; the report-back shape is locked with lines from the real demo run.
- The repository is now a plugin marketplace: `.claude-plugin/plugin.json` (with an icon) and `.claude-plugin/marketplace.json` for Claude Code, `.codex-plugin/plugin.json` and `.agents/plugins/marketplace.json` for Codex, each pointing at the repo root so `skills/` is the shipped skill. Checked: `claude plugin validate .` and `claude plugin validate .claude-plugin/marketplace.json` pass (one warning: the root CLAUDE.md is not plugin context, as intended); with an isolated `CODEX_HOME`, `codex plugin marketplace add <repo>` and `codex plugin add proofmerge@proofmerge` installed `skills/proofmerge/SKILL.md`. Not checked: installing from GitHub, and an agent using the skill on a real task.
- Tests: `tests/test_skill.py` also checks that both plugins name the skill at the package version, that the Codex skills path and the Claude icon exist, that both marketplaces list only this plugin at the repo root, and that the hard rules come before the steps.

### 2026-11-02 — T62 choose the AI model in the app (Claude)
- The app's Setup page has an **AI model** row instead of "Model key": Meta Muse (the default, Meta key), Claude plan (the plan Claude Code is signed in to, no key; `claude --version` must work), or Other API (an OpenAI-compatible https:// address or http://localhost, a model for each role, and a key). Jobs get `PROOFMERGE_BACKEND`, `PROOFMERGE_BASE_URL`, `PROOFMERGE_SPARK_MODEL` and `PROOFMERGE_WONDER_MODEL` from the choice; an empty value falls back to Meta's endpoint and Muse models, so choosing Meta also clears environment overrides. With no choice made, the environment decides, as before.
- The choice is saved in `~/ProofMerge/settings.json` and read back on start; the key stays in memory only. A hand-edited file that fails the checks is ignored. Addresses with credentials, plain http to another host and other schemes are refused; model names are checked against a strict pattern.
- Codex plans are not offered: Codex's shell tool cannot be switched off (T49 canary), so repository text could make it read local files.
- Checked: `tests/test_dashboard_model.py` (12 cases: defaults, Claude readiness with and without the CLI, the API mapping, refused inputs that leave the choice unchanged, Meta clearing overrides, saving without the key and reading back); the three choices and a refused address in headless Chrome at 1440 px and 375 px, with an isolated HOME. Not checked: a real `verify` through the Claude plan or another API from the page.

### 2026-11-02 — T63 JavaScript and TypeScript set up automatically (Claude)
- `src/proofmerge/node_project.py`: with no `.proofmerge.toml`, a base commit with `package.json` and `package-lock.json` at the root and no Python project files gets a generic-runner config ProofMerge writes itself. The image `proofmerge-node:<sha256 of Dockerfile, package.json and lock>` is built from `node:22-slim` with `npm ci --ignore-scripts` plus `tsx`; probes are `node:test` files at the repo root run with `node --import file:///node_modules/tsx/dist/loader.mjs --test` (an absolute path, so a `node_modules/tsx` committed in the change cannot replace the loader), so the repo's Vite/Vitest/Jest config is never loaded; both revisions run with the base commit's `tsconfig*.json`/`jsconfig*.json`, so a change cannot redirect its imports through `paths`; `AssertionError` marks a FAIL. Spark's probe prompt gets a note to use `node:test`/`node:assert` and relative imports; a probe that imports vitest is an execution error.
- Checked by hand before wiring it in (Docker, the sandbox's own flags: no network, read-only, non-root, 64 pids, 512 MB): vitest and tsx both resolve `/node_modules` from a read-only `/work`; tsx with a hostile `vite.config.ts` in the repo never loads it.
- `demo/build_ts_demo.py` and `demo/ts-shop/`: an expense-totals module whose "readability refactor" counts drafts and caps per category instead of per item, while the repo's two vitest tests pass on both branches (checked in Docker).
- Real runs with Muse Spark and Muse Wonder (key from the user's shell, allowed earlier): the first gave BLOCK with H1 drafts counted, H2 per-category cap and H3 negative draft now throwing all verified (base PASS 3/3, head FAIL 3/3), 7 calls; it wrote to a scratch path, so it was run again into `/tmp/proofmerge-demo-ts/expense-totals-refactor`: BLOCK, H1 verified, H2 and H3 REVIEW because Wonder doubted their probes, 7 calls, prompt 8626 / completion 10203 tokens. That bundle is `demo/runs/2026-11-02-ts/expense-totals-refactor`; `proofmerge replay` there: evidence intact (50 files), verdict recomputed BLOCK; no scratch path, user name or key in it.
- Tests: `tests/test_node_project.py` (detection and the Python precedence, the npm lockfile requirement, typescript vs javascript, the tag following the lockfile and the cache, a failed `npm ci`, the prompt note only for this config, and a Docker test that builds the demo and gets base PASS / head FAIL, plus ERROR for a vitest-importing probe).
- Limits: npm only (yarn and pnpm need a `.proofmerge.toml`); no tamper guard for JS/TS; code that imports CSS, images or bundler aliases, and components that need a browser, are not tested.

### 2026-11-02 — T33 the GitHub Action on real pull requests (user + Claude)
- The user created the public repository `imthi16/proofmerge-demo-shop` (the synthetic demo shop plus `.github/workflows/proofmerge.yml` using `imthi16/proofmerge@main`, `fail-on: block`), stored `MODEL_API_KEY` as an Actions secret from stdin, and opened three PRs from neutral `agent/*` branches with the candidates' `intent.md` as their descriptions.
- Results, read from each PR's ProofMerge comment and the `proof.json` in its evidence artifact: #1 `agent/price-cache` BLOCK (H1 verified against PRESERVE claim C0; H2 REVIEW, probe may not test it), 6 calls; #2 `agent/discount-refactor` BLOCK (H1 verified against C1, plus changed tests in `tests/test_pricing.py` failing on head with no CHANGE claim), 5 calls; #3 `agent/bulk-threshold-150` REVIEW (changed tests fail on head, but the contract has a CHANGE claim), 3 calls. Each job took one to two minutes; the BLOCK jobs fail the check as `fail-on: block` asks. The same verdicts as the local demo runs of 2026-10-29.
- The comment body escapes model text as HTML entities; rendered through GitHub's markdown API it reads as plain sentences.

### 2026-11-02 — Walkthrough video in the README (Claude)
- `docs/assets/walkthrough.mp4` (4 min 20 s, 1280×800, no sound, on-screen captions) and a 16-second preview `docs/assets/walkthrough-preview.gif`, linked from "Use it without a terminal".
- Recorded with headless Chrome's screencast, driving the real `proofmerge app` page and server from `main`. Only the outside world was faked: `gh` (sign-in code, account `demo-user`, three repositories, the clone, two PRs), `docker` health answers and `proofmerge verify`, which replays stage events and copies the recorded `demo/runs/2026-10-29/tenant-cache-take2` evidence. Git ran for real on a locally built demo shop. The model key typed on Setup was a fake value; no model was called. The terminal part types commands into a page and prints their real output: `--help` and `replay` were run on `main` that day, and the `verify` output is the recorded run quoted earlier in this README. The README notes what is simulated.

### 2026-11-03 — T67 Linux installer (Claude)
- `installer/linux/install-proofmerge.sh` (POSIX sh): finds Python 3.11+ with venv support, Git, the GitHub CLI (optional) and Docker; for anything missing it prints the distribution's install command (apt-get, dnf, pacman, zypper) and never runs sudo. Installs ProofMerge from GitHub into `${XDG_DATA_HOME:-~/.local/share}/proofmerge/venv` (PATH untouched), with the same force-reinstall-then-deps update as the Windows installer, writes `proofmerge.desktop` and `proofmerge-uninstall.desktop` to the applications folder, then starts `proofmerge-app` detached (skipped with `PROOFMERGE_NO_OPEN=1`). `PROOFMERGE_REF` is checked before it reaches pip; an install folder with a quote, `$` or backslash is refused.
- Real run on Ubuntu 26.04 under dash, in a scratch `HOME` with `env -i`: installed `main` from GitHub in 21 s (`Installed proofmerge 0.1.0`, Docker found running); `desktop-file-validate` passed on both entries; the menu entry's `Exec` command served `/app/` with 200; a second run updated in place and the detached app started (log: `ProofMerge is open at http://127.0.0.1:8765/app/`); `uninstall.sh` removed the venv and both entries and kept `~/ProofMerge/runs`.
- Not tried: clicking the entry in a desktop menu, other distributions, a machine without Python venv or Docker.
- Tests: `tests/test_linux_installer.py` runs the script for real for bad refs and a bad install folder (both stop before any download) and pins no sudo, no piped downloads, and an uninstaller that keeps `~/ProofMerge`.
- Review fixes (Codex, #60): Python is chosen only with venv support, so a later interpreter that has it is used; an install folder with `%` is refused (desktop `Exec` reads it as a field code); the uninstaller first stops this install's running app, found by its venv path in `/proc/*/cmdline`. Re-checked for real: with the app running detached, `uninstall.sh` stopped it, removed the install and kept `~/ProofMerge/runs`. A new test gives the script a PATH whose `python3.13` lacks venv; it fails on the previous script and passes now.
- `pytest -q`: 861 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-03 — T68 Uninstall asks first (Claude)
- Found on the real install: the GNOME menu shows "Uninstall ProofMerge" next to "ProofMerge", and one click on it (journal: `app-gnome-proofmerge\x2duninstall` launched by gnome-shell at 12:08) removed the install without asking.
- `uninstall.sh` now asks `Remove ProofMerge from this computer? ... [y/N]` and removes nothing unless the answer starts with y; an empty answer or closed input keeps everything. It waits for Enter at the end, so the terminal window does not close before the message is read.
- Test: `test_uninstaller_asks_first_and_keeps_the_checks` writes the real uninstaller into a temporary folder and runs it with no answer, `n` and `y`; only `y` removes the install and menu entry, and `~/ProofMerge/runs` stays every time. It fails on the previous script.
- `pytest -q`: 862 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-03 — T69 Faster repository pick in the app (Claude)
- Measured on a real 14 MB repository (2,382 commits, 268 branches): `gh repo clone` took 5.5–6.4 s and a blobless clone 9.5 s, so the download itself was not the slow part to cut. A single-branch clone saved about 2 s but would hide the other branches from the Home branch picker, so it was not used.
- What the app added: every status read ran `gh api user` (0.7–1.1 s), and the page reads status every 1.5 s while a clone runs. The signed-in answer is now reused for 30 s (`WHO_SECONDS`); a signed-out answer is never reused, so signing in elsewhere shows at once, and signing out clears it. The clone poll is 1 s instead of 1.5 s.
- Real timing, same machine and repository, pick to the page seeing "done" (already downloaded, so a fetch): 4.0 s and 4.5 s on `main`, 3.0 s and 3.0 s with this change.
- Test: `test_status_polls_reuse_a_signed_in_answer_but_never_a_signed_out_one` (five polls ask GitHub once; signed-out and after-logout ask every time). It fails on the previous code.
- `pytest -q`: 863 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-03 — T70 Folder picker list stays inside its dialog (Claude)
- Found in the app: on Home, "Choose your project folder" with a full home folder drew the list past the dialog and scrolled the page. Cause: the repository picker's later `.picker { display: grid }` rule also matched the folder dialog, so the list's `flex: 1` and the dialog's height limit no longer held.
- The folder picker rule is now `.dialog.picker`, which outranks it. Measured in headless Chrome at 1920×1080 on a real home folder: before, the list ended at 1247 px and the page was 1301 px tall; after, the list ends at 822 px inside the dialog (bottom 894 px), the footer shows, and the page is 1080 px with no scroll. The dialog is back to its intended 42rem width.
- Test: `test_folder_picker_list_scrolls_inside_the_dialog` pins the scoped rule; it fails on the previous CSS.
- `pytest -q` with T69 merged in: 864 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-03 — T71 App refresh, Markdown descriptions, model names that match the run (Claude)
- Asked for in the app: the look felt dated; Recent checks should sit on the right; the Runs detail made you scroll inside a box; PR descriptions showed raw Markdown in a scroll box; with the Claude plan chosen, a run still said "Spark" and "Wonder".
- Look: neutral graphite surfaces in place of the blue-grey, an azure accent chosen to sit apart from the three verdict hues, a deeper shadow with a top highlight, 14 px panel radius. Light theme moved in step. Geist stays (vendored, offline, and the same face as the report); the design detector flags it as an overused face.
- Home: the check sits on the left and Recent checks is a right-hand column (six runs, each with its verdict, SHAs and first reason) that slides in from the right with its rows after it; reduced motion turns it off. Below 64rem it stacks.
- Runs and Pull requests: the detail now scrolls with the page and the list stays in view beside it; after a pick the detail's top is brought into view when it is out of sight. Beside the detail the PR table drops its branch column (the detail shows it), which had pushed the verdict column out of view.
- PR descriptions render from Markdown (`legacy/markdown.js`): headings, lists and task lists, quotes, tables, code, links. The text is untrusted, so it becomes React elements, never HTML: tags stay text, only http(s) links are linked, images show as links and are not loaded, HTML comments are dropped.
- Model names: `report.model_names` and the app's `runModels` read what the run recorded (`cli:claude-cli` means Claude; a non-Muse model id is shown by name; older proofs mean Meta). The report, the run detail ("Claude wrote and checked the tests"), Wonder annotations and the scope receipt follow it; the live progress steps follow the provider chosen on Setup. The CLI's "Spark wrote no probe" line now says "no probe written".
- Checked in Chrome at 1440×900 (dark and light) and 390×844 with the real Claude run `pr-75-479d672` and this repository's PRs: no horizontal scroll on Home, Runs or Pull requests (the PR page overflowed by 37 px on a phone until the shell and split grids used `minmax(0, 1fr)`).
- Tests: `test_dashboard_markdown.py` (runs `markdown.js` in Node: structure, and `javascript:`/`data:` links, images, raw HTML and comments staying inert) and `test_report_names_the_models_the_run_used`.
- Review fix (Codex, #64): a description of thousands of backticks made the inline pattern backtrack for seconds (10,000 took over 10 s), freezing the page while it rendered. Every inline span now has a bounded length; the same test set found that thousands of nested `>` overflowed the call stack, so quote nesting stops at 8 levels. `test_hostile_descriptions_render_in_linear_time` runs ten such inputs (20,000–80,000 characters, each under 0.2 s); the backtick case fails on the previous parser.
- `pytest -q`: 877 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-03 — T72 README app screenshots in the refreshed design (Claude)
- `docs/assets/app-runs.png` and `docs/assets/app-github-sign-in.png` re-captured from the refreshed app (headless Chrome, 1280×800 at 1.5625×, dark), same content as before: the Runs page on copies of the committed demo runs (three BLOCK, one REVIEW, all evidence intact; the committed bundles were not touched), and the Setup GitHub row while signing in. For the sign-in card `gh` was a stand-in script that prints the one-time code `06F2-E201`; nothing reached GitHub.
- Found while capturing: beside the detail, the Base → head column squeezed run names to a few characters a line at 1280 px. Between 64rem and 110rem that column now hides, as the PR branch column already does; the detail shows both SHAs.
- The walkthrough video and its GIF were not re-recorded (the recording harness is not in the repository); the README now says they predate the visual refresh.
- `pytest -q tests/test_dashboard*.py`: all passed. `ruff check src tests`: all checks passed.

### 2026-11-03 — T73 Runs page: verdict under the list, evidence beside it (Claude)
- Found in the app: with one run, the Runs page was a short list on the left and a long detail on the right, so the left half stood empty for most of the page.
- The run detail is now two panels in a grid: the summary (verdict, reasons, commits, models, actions) sits under the run list and stays in view while scrolling; the evidence (hypotheses and probes, test-tamper guard, intent claims) takes the right column. The narrow list keeps verdict, name and regressions; the SHAs and the call count are in the summary. Below 64rem everything stacks: list, summary, evidence.
- Checked in Chrome at 1440×900, 1280×800 and 390×844 with the real Claude run `pr-75-479d672`: no horizontal scroll; after scrolling 1,500 px the summary stays 24 px from the top on both wide sizes. `docs/assets/app-runs.png` re-captured in this layout (copies of the committed demo runs, as in T72).
- `pytest -q tests/test_dashboard*.py`: all passed. `ruff check src tests`: all checks passed.

### 2026-11-03 — T74 Every app page checked at three sizes (Claude)
- Audit: Home, Runs, Pull requests, Benchmarks and Setup in Chrome at 1440×900, 1280×800 and 390×844, dark and light (30 captures), with copies of the demo runs, the run 3 benchmark and the real Claude run, and this repository's PRs. Each capture measured page overflow and elements wider than their box.
- Desktop: no overflow on any page. Phone fixes:
  - The top bar pushed Theme and Quit off screen: the brand text was a bare text node, so the rule that hides it on phones never matched; it is now a span.
  - Benchmarks scrolled the page sideways by 31 px: the tables now use tighter cells on phones, the panel scrolls inside itself if it must, and "16 / 16" no longer wraps.
  - Pull requests hid the verdict column: the title's 12rem minimum now applies above phone width only.
- After the fixes all 30 captures measure zero overflow.
- `pytest -q tests/test_dashboard*.py`: all passed. `ruff check src tests`: all checks passed.

### 2026-11-03 — T82 App icon (Claude)
- The author's AI-generated logo is now the app's tab icon (`<link rel="icon">`) and the sidebar mark, served from `dashboard_static/legacy/icon.svg`; the PNG and SVG sources are in `docs/assets/brand/`. The generated SVGs were checked for scripts, links and embedded images: none. Attributed in `ORIGINALITY.md`.
- The generated cover banners (`docs/assets/brand/cover-*.png`) are now the README's top image. Their verification panel is a redrawing, labelled DEMO RUN, of the README's real demo run: the same commits 9dcc104 and 32dff53, base 3 of 3 PASS, head 3 of 3 FAIL, BLOCK. The first version of this entry wrongly called those numbers invented; they were checked against the previous README hero. The old `hero-*.png` files stay in `docs/assets/`, unused.
- Not checked: the icon was not looked at in the running app.
### 2026-11-03 — T75 Suggested fixes, checked by their probe, and posting to the PR (Claude)
- `proofmerge fix <proof.json> --repo <repo>` (`fixes.py`, prompt `prompts/fix.md`): for each verified regression (at most 3), the writer model proposes new content for files the PR changed. It may not edit tests, files outside the PR, or more than 5 files. A probe Muse Wonder doubted, or one that may show the intended change, gets no fix. The fix is built with git plumbing as a commit under `refs/proofmerge/fix/<head>/<H>` (no checkout; branches and the working tree untouched). The failing probe then runs again in the sandbox on base and on the fixed head; **verified** only if it passes every attempt on both. A second draft sees why the first was rejected or failed. Results go to `<run>.fix/fix.json` beside the evidence, so the sealed manifest and `replay` are unchanged; the verdict never changes.
- `proofmerge post <proof.json> --pr <n> --repo <repo> --mode inline|overall [--dry-run]` (`pr_review.py`): one GitHub review with event COMMENT (never approve or request changes). Inline mode turns each change into a `suggestion` block on the PR's lines, anchored only to head-side lines of GitHub's own PR diff (`gh pr diff`); a change outside it goes in the summary as a diff. A fix shared by two regressions is posted once, naming both. Model text has `@` defused so it pings nobody.
- App: on a BLOCK run, **Suggest a fix** (job `fix`) and, for runs started from Pull requests, **Post to PR #n** with a confirm dialog that picks inline or overall (job `post`). Each hypothesis shows its fix: verified or not and why, the explanation, and the patch.
- Real runs with the Claude CLI backend:
  - A locally built demo shop, `tenant-cache-regression`: verify gave BLOCK (H1, H2 verified; H3 needed review), 7 calls. `fix` on that run, before the doubted-probe rule, fixed all three, each verified with base PPP and fixed PPP, 3 calls. H1's fix keys the cache by `(tenant_id, sku)` and keeps the caching.
  - `imthi16/proofmerge-demo-shop` PR #1 fetched from GitHub: verify gave BLOCK (H1, H2 verified), 6 calls. `fix` gave two verified fixes (base PPP, fixed PPP), 2 calls. `post --dry-run` against GitHub's PR diff built three inline suggestions (lines 9, 14–15, 22) labelled H1, H2. Overall mode built one comment with the diff. **Not posted**: nothing reached the PR.
- Tests: `test_fixes.py` (test-file rules, proposal checks, the fix commit leaves branches and the working tree alone, verified only on all-pass, a second draft after a failure, doubted probes skipped, evidence manifest untouched), `test_pr_review.py` (diff anchors, insert and replace ranges, inline and overall bodies, out-of-diff fallback, shared fix posted once, gh call and refusal), and a job test for `fix` and `post` inputs.
- `pytest -q`: 890 passed, 1 skipped. `ruff check src tests`: all checks passed.

### 2026-11-03 — T76–T81 App fixes found in manual testing, Approve, Ponytail review, GitHub accounts (Claude)
- T76: the confirm and picker dialogs scrolled away with the page. `.page` and `.detail` used `animation: … both`; Chrome keeps a filling transform animation as the containing block of `position: fixed` children. Both now use `backwards`. Headless Chrome after a 1500 px scroll: dialog top -50 with `both`, 207 (centred) with `backwards`. `test_dialog_stays_fixed_to_the_window` fails on the old CSS.
- T77, T78: the Pull requests list asked `gh pr list --state all --limit 50`, so merged and closed PRs filled it and older open PRs never reached the page's search box. It now asks `--state open --limit 1000`. On a real repository it loaded all 95 open PRs (93 KB, 3 s).
- T79: `proofmerge approve <proof.json> --pr <n> --repo <repo> [--dry-run]` and an **Approve PR #n** button with a confirm dialog on PASS runs. It posts an APPROVE review on the commit the run checked and refuses when the evidence changed, the PR is not open, or its head moved since the run. `post` still only comments.
- T80: `proofmerge ponytail <proof.json> --repo <repo>` and a **Ponytail review** button: one model call on the diff lists what could be deleted or shortened (delete, stdlib, native, yagni, shrink). It reads the diff only, never changes the verdict, and writes `<evidence dir>.ponytail/ponytail.json`. The rules adapt the ponytail-review skill (MIT, Copyright (c) 2026 DietrichGebert), attributed in `ORIGINALITY.md`. One real call with the Claude CLI backend on a real diff returned four findings.
- T81: Setup lists every account gh holds, switches between them (`gh auth switch`) and adds another (`gh auth login` keeps the existing ones). Switching is refused while a check runs and clears the pull request list cache. The account parser read both accounts of a real `gh` install; the sign-in itself was not exercised.
- Not checked: the new buttons and dialogs were not exercised end to end in the browser by the author of this entry; `Approve` has never posted a real review.
- `pytest -q --ignore=tests/test_docker_sandbox.py`: 897 passed, 1 skipped. `ruff check src tests`: all checks passed.
### 2026-11-03 — T75 review fixes (Claude)
- `fix` read `test_source_path` and `stdout_path` from `proof.json` without checking them, so a bundle with `../secret.txt`, an absolute path or a symlink could send a host file to the model, and `verify_manifest` would not have caught it. Both reads now go through `evidence.read_evidence`, which refuses a path that leaves the evidence directory; a hypothesis whose probe path leaves it gets a recorded problem and no model call.
- A regression against a contested claim (a REVIEW matter in `verdict`) no longer gets a fix.
- Two regressions that share one patch are still posted once, but each now carries its own probe status; before, one verified probe stood for the whole group.
- Tests: `test_a_probe_path_that_leaves_the_evidence_is_never_read` (parent path, absolute path, symlink), `test_a_regression_against_a_contested_claim_gets_no_fix`, `test_a_shared_fix_keeps_each_probes_own_status`. `pytest -q --ignore=tests/test_docker_sandbox.py`: 893 passed, 1 skipped. `ruff check src tests`: clean.

### 2026-11-03 — T79 review fixes (Claude)
- `approve` trusted the manifest alone: a BLOCK bundle relabelled PASS with a regenerated manifest would have been accepted. It now recomputes the verdict with `rederive`, as `replay` does, and refuses a decision that does not follow from the evidence, or a run that predates recorded verdict inputs.
- A PR retargeted to another branch keeps its head SHA, so the head check passed while the diff and the required checks differed. `approve` now also requires the base this run checked to still be part of the branch the PR targets (`gh api compare/<base>...<target>` answers identical or ahead).
- Tests: `test_an_edited_pass_cannot_approve_even_with_a_new_manifest` (real pipeline run, relabelled and resealed), and the approve refusals now cover a retargeted base. `pytest -q --ignore=tests/test_docker_sandbox.py`: 904 passed, 1 skipped. `ruff check src tests`: clean.

### 2026-11-03 — T82 review fix (Claude)
- The logo only reached the legacy page (`/app/`); `/` and the Control Center routes serve `app/index.html`, which had no icon and a text-only brand. `ui/index.html` now links `ui/public/icon.svg`, `App.tsx` shows it in the header, and the committed build was regenerated (`npm run build`). The same sources built before the change reproduced the committed hashes exactly, so the CI "committed build matches" check holds.
- `npm run lint`, `typecheck` and `npm test` (20 tests) pass locally. The Playwright e2e was not run locally; CI runs it.

### 2026-11-03 — T83 Applications menu icon (Claude)
- The Linux installer wrote `Icon=applications-development`, a stock icon. Step 6 now copies the app's logo out of the installed package to `<install folder>/proofmerge.svg` and the menu entry points at it, so it does not depend on the Python version's folder and the uninstaller removes it with the install folder. If the copy fails the entry falls back to the stock icon.
- Run for real with this branch's installer on Ubuntu (against a local clone of the repository): the install finished, `Icon=` in `proofmerge.desktop` points at the copied `proofmerge.svg`, and `desktop-file-validate` accepts the entry. Not checked: how the icon looks in the desktop's menu, and a fresh install from GitHub.

### 2026-11-03 — T83 closable alerts and Recent checks (Claude)
- Error boxes (a refused action, a failed sign-in, a failed clone, a branch list error) had no close button and stayed until the page changed. They are now an `Alert`: it has a close button and closes itself after 10 seconds.
- A card on Home's Recent checks could not be removed. Each card now has a close button and the panel a **Clear** link; the choice is kept in this browser (`localStorage`, `pm-hidden-recent`) and only hides the card there, the run stays under Runs.
- Checked in real Chrome (Playwright with the system Chrome) against this branch's dashboard on a copy of a real run folder: closing a card, the card staying closed after a reload, **Clear** removing the panel, an alert (a faked 422 on `/api/github/add`) closing with its button and closing by itself after about 10 s, and no page errors. Not checked: dark mode and a phone-width screen.

### 2026-11-03 — T83 fix: the Pull requests tab went black (Claude)
- Cause: the closable-alert change put a `useState` in `RepoPicker` after its early `return`s, so once `gh` loaded React threw error #310 ("rendered more hooks than during the previous render") and the tab showed nothing. Reproduced in real Chrome (the prs tab: 0 characters of text, error #310); the hook now sits with the others at the top.
- Re-checked in real Chrome: all five tabs (Home, Runs, Pull requests, Benchmarks, Setup) render with no page errors, and the Pull requests list with a repository (1 open PR row) renders with none. The earlier check had covered Home and Setup only.
- `test_repo_picker_declares_every_hook_before_its_early_returns` keeps it from coming back.

### 2026-11-03 — T84 README table, fresh install from GitHub (Claude)
- Fresh install: the Linux installer from `main` was run with an empty `HOME` and `XDG_DATA_HOME`. It installed from GitHub, the menu entry's `Icon=` pointed at the copied `proofmerge.svg`, and the app started from the entry's command and answered 200 for `/app/`, `/`, `/static/legacy/icon.svg`, `/static/app/icon.svg` and `app.js`. Not checked: how the icon looks in a desktop's menu.
- README "What works today" now has rows for Approve, Ponytail review and several GitHub accounts, each with what was and was not run, and the Linux installer row records this check.

### 2026-11-03 — T86 Runs page: one reading column beside a list rail (Claude)
- Problem (author's screenshot): the Runs list sat on top-left, the verdict card under it and the evidence on the right, so the eye zigzagged and the verdict was far from its evidence. The BLOCK/PASS tiles above (a big number with a small label) also took a fifth of the screen.
- New structure in `legacy/app.js` and `app.css`: the list is a sticky rail on the left; on the right one run reads top to bottom: the verdict with its reasons (and, at 88rem and wider, the run's facts beside them), the actions, then the evidence. The verdict counts are now small filter chips on the search row. No sticky summary any more. Below 64rem it stacks: list, verdict, evidence.
- Checked in real Chrome with the system Chrome on the committed demo runs: 1600, 1100 and 390 px wide in light mode (no horizontal overflow, no page errors), dark mode, and a verdict chip filtering the list. The Impeccable layout detector reported nothing.
- Not checked: the Control Center (`/`) Runs page, which is separate code and unchanged, and the README's Runs screenshot (`docs/assets/app-runs.png`), which still shows the old layout.
### 2026-11-03 — T85 Large pull requests are fitted, not refused (Claude)
- `verify`, `hypotheses` and `probe` stopped with "diff is over 60000 characters". New `diffbudget.view_diff` fits the diff to the same 60,000 characters: source files first, then tests, small files first, never lock, generated or binary files; a note in the prompt names what was left out. Spark is told to write hypotheses only about code it can see.
- The verdict records what it did not see in `VerdictInputs` (`unshown_files`, `unshown_other`), so `replay` recomputes the same verdict. Source files not shown are named in the scope and turn a PASS into REVIEW ("part of the change was never examined"); BLOCK and REVIEW are unchanged. Test, lock, generated and binary files not shown are only noted, because the tamper guard re-runs the original tests anyway. `fix` and the Ponytail review use the same view.
- Real run: the author's pull request imthi16/helpdeck #73, 16 files, 65,582 characters (it was refused before). 15 of 16 files were shown; the one left out was a test file. `verify` finished with exit 0: 5 hypotheses, 12 calls, REVIEW. No probe ran, and the 4 changed tests errored in the sandbox (that repository's dependencies are not installed there), so the verdict says nothing about the pull request itself.
- Tests: `tests/test_diffbudget.py` (13: small diff whole, source first, one huge file shown in part, non-git text cut not dropped, generated files, PASS to REVIEW for unshown source, a PASS kept for unshown tests or generated files, recorded in the proof and replayed). The refusal tests were removed. The API schema and generated types were regenerated for the new fields. `pytest -q --ignore=tests/test_docker_sandbox.py`: 915 passed, 1 skipped. `ruff check src tests`: clean.
- Not checked: the Docker sandbox tests locally; a large pull request on a repository whose tests run in the sandbox.

### 2026-11-03 — T85 review fixes (Claude)
- `adjudicate` replaced `decide()`'s PASS with REVIEW when source files were not shown, a second decision authority next to the rule that every verdict comes from `decide()`. The policy now lives in `decide()` (`unshown_source_files`): it adds a reason, so a PASS becomes REVIEW and a BLOCK stays BLOCK with every reason kept. `adjudicate` only passes the list in and adds the scope note. A REVIEW or BLOCK now also carries that reason, which the first version did not add.
- The omission note named every excluded path and was not counted against the 60,000-character budget: one 59,000-character source change plus 5,000 generated files gave a 954,247-character prompt. The selection now reserves 3,000 characters for the note and the note names at most 15 paths per list; the complete lists stay in `DiffView` and in the proof's inputs.
- Tests: `test_the_prompt_stays_within_the_limit_however_many_files_were_left_out` (5,000 long vendor paths), `test_a_regression_still_blocks_when_source_was_not_shown`.

### 2026-11-03 — T87 Ponytail review moves to the Pull requests page (Claude)
- The Ponytail review was a button on a finished run, so it needed a `verify` first (Docker, model calls) although it only reads the diff. It is now a button on every open pull request on the Pull requests page: it fetches the PR into `refs/proofmerge/`, then `proofmerge ponytail-diff` makes one model call on the diff (the PR title and body are the intent) and the findings show under the PR's actions. The result is saved per head commit in `<runs folder>/ponytail/pr-<n>-<sha>.json`, so a PR that changed gets a fresh review. It has its own job state, apart from verify's, and no confirm dialog (one call, nothing runs). The button and findings were removed from the Runs page; `proofmerge ponytail <proof.json>` stays for a finished run.
- Real run in Chrome (system Chrome through Playwright, Claude Code backend) on the author's repository: PR #75 (+83/-1) finished in 19 s and PR #74 (+14,984/-712, far over the diff budget) in 12 s, both showing findings with no page errors.
- Tests: two dashboard tests (the review runs without any verify call and its result is listed on the PR; a failed review says why and a merged PR is refused). Account switching is also refused while a Ponytail review runs.
- Not checked: the Control Center (`/`) Pull requests page, which has no Ponytail button; the Docker sandbox tests locally.

### 2026-11-03 — T88 Runs page: the evidence stays in the full report (Claude)
- The author asked for the detail on the right of the Runs tab to go, since the full report already has it. The evidence panel (hypotheses and probes, tamper guard, intent claims) is removed from the Runs page; `report.html` and its **Open full report** button are unchanged. Suggested fixes were shown inside the hypotheses, so they now have their own "Suggested fixes" block under the actions and stay visible. The run's verdict, reasons and facts stay.
- Checked in real Chrome on a copy of the demo runs with a fix result written beside one run: no evidence panel and no hypothesis cards, the report button and the fix block present, no horizontal overflow at 1600 and 390 px, no page errors.
- Not checked: the Control Center (`/`) Runs page, which is separate code.

### 2026-11-03 — T90 Post and Approve say which GitHub account they act as (Claude)
- On the Runs page nothing said which of the signed-in GitHub accounts **Post to PR** or **Approve PR** would use. Both buttons now end with "as @account" (the active `gh` account, read from `/api/github` when the run opens), and both confirm dialogs start with an "Acting as @account" line with **Use another account** (to Setup) when more than one account is signed in. Without a known account the line says "your signed-in GitHub account".
- Checked in real Chrome against this branch's dashboard with the author's real `gh`: the button read "Post to PR #7 as @mdimthi" (the account that was active then), the dialog showed the line and the link, no page errors; the account appeared about 1 s after the run opened. Cancelled without posting. Not checked: the Approve dialog (no PASS run in that test), and the Pull requests page, whose Verify uses the account only to read.
