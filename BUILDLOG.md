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
