# Multi-language support via `.proofmerge.toml`

## JavaScript and TypeScript: set up automatically

A repository with `package.json` and `package-lock.json` at its root, no
`.proofmerge.toml` and no Python project files (`pyproject.toml`, `setup.py`,
`setup.cfg`, `requirements.txt`, `Pipfile`) needs no config. ProofMerge reads both
files from the base commit and:

- builds an image `proofmerge-node:<hash>` from `node:22-slim` with
  `npm ci --ignore-scripts` plus `tsx`, so no script from the repository runs while it
  builds; the network is used only for that build, never in a probe container, and
  each lockfile is built once;
- has Spark write each probe as one Node test-runner file at the repository root
  (`node:test` and `node:assert`), run with `node --import <the image's tsx loader> --test`.
  The loader is named by an absolute path in the image, so a `node_modules/tsx`
  committed in the change cannot replace it. The repository's own Vite, Vitest or
  Jest config is never loaded, so it cannot change how a probe runs. A probe that
  imports a test framework is an execution error, never evidence;
- runs both revisions with the base commit's `tsconfig*.json` and `jsconfig*.json`
  files, so a change cannot redirect its own imports through `paths`.

The language is `typescript` when the base commit has `tsconfig.json` or depends on
`typescript`, otherwise `javascript`. Only npm lockfiles are read; for yarn or pnpm,
write a `.proofmerge.toml` as below. Probes import code by relative path, so code
that imports CSS, images or a bundler-only alias is an execution error, and
components that need a browser are not tested.

```bash
python3 demo/build_ts_demo.py /tmp/proofmerge-ts-demo
proofmerge verify /tmp/proofmerge-ts-demo --base main --head candidate/expense-totals-refactor \
  --intent-file demo/ts-shop/candidates/expense-totals-refactor/intent.md --evidence-dir /tmp/ts-evidence
```

The recorded run is in `demo/runs/2026-11-02-ts/`: BLOCK, with H1 ("Draft expenses now
counted toward total") passing 3/3 on base and failing 3/3 on head, while the
repository's own vitest suite passes on both. Muse Wonder judged that the probes for
H2 and H3 may not test their hypotheses, so those two are REVIEW. An earlier run on
the same demo found all three as verified regressions; model output varies between
runs.

## Any language through `.proofmerge.toml`

A repository can describe how ProofMerge should test it by adding `.proofmerge.toml` at the repository root. The file is read from the **base** commit, so a pull request cannot change how it is tested.

A `[python]` table is separate from this runner. It keeps the Python pytest runner and only sets import paths and an optional hashed install. See [python-projects.md](python-projects.md). Do not combine `[python]` with the `language` key below.

## Config fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `language` | string | yes | — | Language identifier, e.g. `go`. Must match `[a-z0-9+#-]{1,20}`. |
| `image` | string | yes | — | Docker image that already contains the language toolchain and dependencies. ProofMerge does not build or pull it. |
| `probe_path` | string | yes | — | Repo-relative path where the probe file is written, e.g. `pricing/proofmerge_probe_test.go`. Must be relative, contain no `..`, and use only `[A-Za-z0-9_./-]`. |
| `test_command` | list of strings | yes | — | Non-empty argv run in the repo root inside the container, e.g. `["go", "test", "-count=1", "-run", "TestProofMergeProbe", "./pricing"]`. |
| `assertion_failure` | string | yes | — | Regex; a non-zero exit is treated as a test **FAIL** only if this matches stdout or stderr. Otherwise it is an execution **ERROR**. |
| `pass_exit_codes` | list of integers | no | `[0]` | Exit codes that mean PASS. |
| `tmpfs_mb` | integer | no | `256` | Size of the writable `/tmp` tmpfs, between 16 and 2048 MB. |
| `env` | table of strings | no | `{}` | Extra environment variables passed to the container. Keys must match `[A-Z_][A-Z0-9_]*`. Useful for `GOCACHE=/tmp/gocache`, `HOME=/tmp`, `GOFLAGS=-buildvcs=false`. |

Example for Go:

```toml
language = "go"
image = "golang:1.23-alpine"
probe_path = "pricing/proofmerge_probe_test.go"
test_command = ["go", "test", "-count=1", "-p=1", "-run", "TestProofMergeProbe", "./pricing"]
assertion_failure = "FAIL: TestProofMergeProbe"

[env]
GOCACHE = "/tmp/gocache"
HOME = "/tmp"
GOFLAGS = "-buildvcs=false"
```

## Isolation

Config-defined runs use the same container isolation as Python runs:

- No network (`--network none`).
- Read-only root filesystem.
- Writable `/tmp` tmpfs sized by `tmpfs_mb` (mounted with `exec` so compiled test binaries can run).
- Dropped capabilities, no new privileges, non-root user (`65534:65534`).
- PID, memory, CPU and time limits.
- `PROOFMERGE_JOB_ID` container label when the variable is set.

## Limitations

- The test-tamper guard is Python-only; for other languages it is skipped and a scope limitation is recorded.
- The Python-only `private_names` heuristic is not applied to generated probes; prompts ask the model to use public names only.
- The repository Docker image must already contain the language toolchain and dependencies; ProofMerge does not fetch anything inside the container.
- Network is disabled inside the container, so dependencies cannot be downloaded at test time.
- Name the probe in `test_command` (Go: `-run TestProofMergeProbe`). A test the command does not select never runs and reads as PASS.
- A non-assertion crash that the test framework reports as a failure counts as FAIL. In Go, a panic inside the probe prints `--- FAIL: TestProofMergeProbe`, unlike the Python runner, where any exception other than an assertion is ERROR.
- Go has been tried on the synthetic demo below, and JavaScript/TypeScript through the automatic set-up above; other languages have not been tried.

## Go demo

```bash
python3 demo/build_go_demo.py /tmp/proofmerge-go-demo
docker pull golang:1.23-alpine
proofmerge verify /tmp/proofmerge-go-demo --base main --head candidate/go-tenant-cache \
  --intent-file demo/candidates-go/go-tenant-cache/intent.md --evidence-dir /tmp/go-evidence
```

The recorded run is in `demo/runs/2026-10-30-go/`: BLOCK, with a Spark-written Go probe that passed 3/3 on base and failed 3/3 on head, while the repository's own tests pass on both.
