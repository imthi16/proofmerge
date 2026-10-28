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

### Next task
- Oct 29: base/head worktrees and isolated test execution with hashes.
- T8 test-tamper static checks (Codex) is still open.
