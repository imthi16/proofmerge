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
$ PYTEST_ADDOPTS= /home/imthiyas/Documents/hackathon/proofmerge-T3/.venv/bin/python -m pytest -q
137 passed in 11.94s

$ /home/imthiyas/Documents/hackathon/proofmerge-T3/.venv/bin/ruff check src tests
All checks passed!
```
- Demo check rebuild + `python demo/check_demo.py` (exit 0): main PASS/PASS; intended-change PASS / n/a / FAIL; tenant-cache-regression PASS/FAIL/PASS; test-tamper PASS/FAIL/FAIL — same table as before batch 2.
- Not verified here: no Spark probe runs against the new candidates yet (that is the batch-2 benchmark run, not the fixture task); TASKS.md is outside this worktree and was not edited by this agent.

### T22 Benchmark and demo leakage cleanup — 2026-10-29
- Neutralized cache docstrings and the boundary-test skip reason; removed ticket/approval cues and mutation disclosure from intents while preserving explicit intended changes and the newsletter fix description. Labels and candidate executable logic are unchanged. Added a separate tenant-b price assertion to the tenant-validation ground truth.
- Build/check commands passed (exit 0): `PY=../proofmerge-T3/.venv/bin/python`; `B=$(mktemp -d)/bench && $PY demo/build_demo_repo.py $B --candidates bench/candidates && $PY demo/check_demo.py $B --ground-truth bench/ground_truth`; `D=$(mktemp -d)/demo && $PY demo/build_demo_repo.py $D && $PY demo/check_demo.py $D`.
- Clean `git archive main` snapshot at `ccc3c033f78479cdadff71d3d0877620f2dfe24a`: demo table matches exactly; all 8 bench candidate rows match. Main lacks 12 newer candidates, so full bench table equality with main cannot be established. The full 20-candidate bench table exactly matches an unmodified `git archive HEAD` snapshot at `e188f4bfc0da19d6f16348e4ab463c7f686f1cdd`.
- `PYTHONPATH=src ../proofmerge-T3/.venv/bin/python -m pytest -q` (exit 0): `238 passed, 7 skipped in 8.19s`.
- `../proofmerge-T3/.venv/bin/ruff check src tests` (exit 0): `All checks passed!`; `git diff --check` passed.

