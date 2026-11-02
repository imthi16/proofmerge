# Multi-language support via `.proofmerge.toml`

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
- Only Go has been tried, on the synthetic demo below.

## Go demo

```bash
python3 demo/build_go_demo.py /tmp/proofmerge-go-demo
docker pull golang:1.23-alpine
proofmerge verify /tmp/proofmerge-go-demo --base main --head candidate/go-tenant-cache \
  --intent-file demo/candidates-go/go-tenant-cache/intent.md --evidence-dir /tmp/go-evidence
```

The recorded run is in `demo/runs/2026-10-30-go/`: BLOCK, with a Spark-written Go probe that passed 3/3 on base and failed 3/3 on head, while the repository's own tests pass on both.
