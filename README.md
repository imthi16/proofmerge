<p align="center">
  <img src="docs/assets/banner.svg" alt="ProofMerge: the same test runs on the original code and on the patch. Base passes, patch fails, so the verdict is BLOCK." width="100%">
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Runtime model: Muse Spark 1.3" src="https://img.shields.io/badge/Muse%20Spark-1.3-6CA0FF">
  <img alt="Runtime model: Muse Wonder 1.0" src="https://img.shields.io/badge/Muse%20Wonder-1.0-8B7CF6">
  <img alt="Track: Indie Developer" src="https://img.shields.io/badge/track-Indie%20Developer-2C3E52">
  <img alt="Status: early build" src="https://img.shields.io/badge/status-early%20build-E3B341">
</p>

ProofMerge checks a pull request written by an AI coding agent. It doesn't
ask a second AI whether the code "looks good". It tries to find a test that
passes on the original code and fails on the patch, runs that test on both
versions, and reports what actually happened.

Built solo for the Meta Global AI Developer Hackathon 2026 (Indie Developer
track). Runtime intelligence: Meta Muse Spark 1.3 and Muse Wonder 1.0.

> [!IMPORTANT]
> **Early build.** The parts marked 🟢 under [What works today](#what-works-today)
> are built and tested. The end-to-end `proofmerge verify` command is not built yet,
> and nothing here describes a feature as working unless it is.

## 🔍 The problem in one table

The repo ships a small demo shop and three pull requests an AI agent might
open. The shop's own test suite passes on all three. Two of them are broken.

This is real output from `python demo/check_demo.py`:

```text
branch                              existing suite  ground-truth test  base tests on head
--------------------------------------------------------------------------------------
main                                PASS            PASS               n/a
candidate/intended-change           PASS            n/a                FAIL
candidate/tenant-cache-regression   PASS            FAIL               PASS
candidate/test-tamper               PASS            FAIL               FAIL
```

| | Pull request | What it claims | What it really does |
|---|---|---|---|
| 🔴 | `tenant-cache-regression` | "Add caching to price lookup for speed. Returned prices must not change." | Caches prices by product only, so one tenant can be charged another tenant's price. |
| 🔴 | `test-tamper` | "Refactor discount calculation for readability. No behavior change intended." | Stops giving the discount at exactly 100, then deletes the test that checked it, so the suite stays green. |
| 🟢 | `intended-change` | "Raise the bulk discount threshold from 100 to 150." | Exactly that. The old test fails because the rule changed on purpose. It must not be blocked. |

Here is the whole first bug. The cache key is the product alone, so the first
tenant to look up a price sets it for everyone:

```diff
+_price_cache: dict[str, float] = {}
+
 def price_for(tenant_id: str, sku: str) -> float:
+    if sku in _price_cache:
+        return _price_cache[sku]
     ...
-    return table[sku]
+    price = table[sku]
+    _price_cache[sku] = price
+    return price
```

A green CI badge would wave all three through. A naive "old test now fails,
so block it" rule would wrongly block the third one. ProofMerge has to tell
them apart.

> [!NOTE]
> The ground-truth tests in `demo/ground_truth/` are written by hand and are
> never shown to a model. They are only used to score ProofMerge.

## ⚙️ How a verdict is reached

```mermaid
flowchart LR
    A["Pull request<br/>base + head + diff"] --> B["Muse Spark<br/>testable guesses + small tests"]
    B --> C["Run on base"]
    B --> D["Run on patch"]
    C --> E["Plain-code classifier"]
    D --> E
    E --> V{"Verdict"}
    E --> W["Muse Wonder<br/>summarises the evidence"]
    V --> R["Report + proof.json + hashes"]
    W --> R

    classDef meta fill:#6CA0FF,stroke:#3B6FD8,color:#0B1A2E
    classDef wonder fill:#8B7CF6,stroke:#6352D9,color:#ffffff
    classDef base fill:#3FB950,stroke:#2A8A3B,color:#0B1A2E
    classDef patch fill:#F0524F,stroke:#C23532,color:#ffffff
    classDef code fill:#E3B341,stroke:#B88A1E,color:#0B1A2E
    classDef plain fill:#1D2B3C,stroke:#2C3E52,color:#EEF2F6
    class B meta
    class W wonder
    class C base
    class D patch
    class E,V code
    class A,R plain
```

1. Read the base and head commits and the diff between them.
2. Ask Muse Spark for specific, testable guesses about what the patch could
   break, each with a small test that would show it.
3. Run each test on the base version and on the patched version, separately
   and in isolation.
4. Classify the results with plain code, not a model.
5. Ask Muse Wonder to summarise the recorded evidence. It sees the test
   results, not Spark's opinion, and it cannot change them.
6. Write a report, a `proof.json` file and hashes of every file used.

How results map to a decision:

| Base | Patch | Meaning |
|---|---|---|
| 🟢 pass | 🔴 fail | The patch introduced a failure. **BLOCK** if it repeats. |
| 🔴 fail | 🟢 pass | Possibly a fix. Not a regression. |
| 🔴 fail | 🔴 fail | The problem was already there. Not a regression. |
| 🟢 pass | 🟢 pass | This test found nothing. That is not proof of correctness. |
| 🟡 error, timeout or flaky | any | **REVIEW** until resolved. |

The three verdicts:

| Verdict | When |
|---|---|
| <img alt="BLOCK" src="https://img.shields.io/badge/-BLOCK-F0524F?style=for-the-badge"> | At least one reproducible failure that the patch introduced. |
| <img alt="REVIEW" src="https://img.shields.io/badge/-REVIEW-E3B341?style=for-the-badge"> | Anything uncertain: a failed setup, a flaky test, an API error, too little context. The default when in doubt. |
| <img alt="PASS" src="https://img.shields.io/badge/-PASS-3FB950?style=for-the-badge"> | Every check ran and none found a regression. Reported as "no regression found within the tested scope", never as "proven correct". |

> [!IMPORTANT]
> A model can push a verdict toward REVIEW. It can never push one to BLOCK or
> PASS. Exit codes, commands and commit hashes are the evidence; a model's
> confidence is not.

## 🧾 The report

`proofmerge report` turns a run's `proof.json` into one static `report.html`: no
server, no JavaScript, nothing loaded from the network. Each hypothesis gets one card with the PR sentence it
relies on, the same probe run 3 times on each commit, and why head failed.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/report-card-dark.png">
  <img alt="Report card for H1: it cites claim C2, 'Returned prices must not change.' A grid shows the probe passing on base 1e6c838 in 3 of 3 attempts and failing on head 6796cac in 3 of 3, with the head failure 'assert 25.0 == 35.0', the probe source and a reproduce command." src="docs/assets/report-card-light.png" width="720">
</picture>

> [!NOTE]
> This screenshot is rendered from `tests/fixtures/sample_proof_block.json`. Its
> intent claims and hypotheses come from a real Muse Spark run, and the failure
> line comes from a real Docker run of the demo's ground-truth test. The verdict
> and the grid were put together by hand, because the end-to-end pipeline does
> not exist yet. The probe in a real run will be written by Spark.
> [Whole page](docs/assets/report-full-light.png).

Render it yourself:

```bash
proofmerge report tests/fixtures/sample_proof_block.json --out report.html
```

## ✅ What works today

| | Part | State |
|---|---|---|
| 🟢 | CLI skeleton (`proofmerge version`, `proofmerge smoke`) | Built, tested |
| 🟢 | Git context: base/head SHAs, changed files, diff and its SHA-256 | Built, tested |
| 🟢 | Meta API client: schema-checked JSON replies, a hard cap on calls, refuses `-contributor` models, never logs the key | Built, tested, one real Spark call made |
| 🟢 | Demo shop with three candidate PRs and hidden ground truth | Built, checked |
| 🟢 | Intent contract: Spark's claims are kept only if they quote the PR text exactly | Built, tested, real Spark runs |
| 🟢 | Spark hypotheses, each tied to one claim (`proofmerge hypotheses`) | Built, tested, real Spark runs |
| 🟢 | Isolated base/head runs in Docker: no network, no host secrets, read-only code, 3 attempts each | Built, tested; demo gives base PASS 3/3, head FAIL 3/3 |
| 🟢 | Test-tamper check: deleted, skipped, xfailed or weakened tests in the diff | Built, tested |
| 🟢 | `proof.json` format and `report.html` (`proofmerge report`) | Built, tested on sample proofs |
| 🟢 | Benchmark batch 1: 5 faulty and 3 safe PRs with hidden ground truth | Built, checked |
| 🟡 | Spark-written probes that must pass on base before head counts | In progress |
| ⚪ | Verdict engine, Muse Wonder cross-check, `replay` | Planned |
| ⚪ | ProofMerge's own results on the benchmark | Planned |

Day-by-day progress, with the real check results, is in [BUILDLOG.md](BUILDLOG.md).

## 🚀 Try it

You need Python 3.11 or newer and Git.

```bash
git clone https://github.com/imthi16/proofmerge
cd proofmerge
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Check the install:

```bash
proofmerge version      # proofmerge 0.1.0
pytest -q               # offline: no network, no API key needed
ruff check src tests
```

Build the demo repository, then compare its branches against the ground truth:

```bash
python demo/build_demo_repo.py   # creates demo/.build/demo-shop
python demo/check_demo.py        # prints the table shown above
```

> [!TIP]
> Run the build step first. `check_demo.py` fails with "Repo directory does not
> exist" until `demo/.build/demo-shop` has been created.

Optional: check your Meta API access. This makes exactly one real Muse Spark
call and needs your own key.

```bash
export MODEL_API_KEY=...   # your Meta developer key; never commit it
proofmerge smoke
```

## 🗂️ Repository layout

```text
src/proofmerge/
  cli.py        command-line entry point
  context.py    reads base/head commits and the diff from Git
  meta.py       the only code that talks to the Meta API
  intent.py     intent claims, kept only if their quote is in the PR text
  hypotheses.py Spark's failure hypotheses, checked against the diff
  sandbox.py    runs a probe on base and head inside Docker
  tamper.py     finds tests the PR deleted, skipped or weakened
  evidence.py   the proof.json format
  report.py     renders proof.json as report.html
  prompts/      the instructions given to Muse Spark
tests/          offline tests; models are replaced with fakes
docker/runner/  the image probes run in (Python + pytest only)
demo/
  demo-shop/    the small example project
  candidates/   the three pull requests and their descriptions
  ground_truth/ hand-written tests and expected labels, never shown to a model
bench/          more candidate PRs and their hidden ground truth, same layout
docs/
  PROJECT_SPEC.md    what ProofMerge must do, and its rules
  SPRINT_PLAN.md     the daily plan up to the deadline
  META_API_NOTES.md  Meta API facts, each with a link to the official docs
  PRIOR_ART.md       related research and tools, and how ProofMerge differs
BUILDLOG.md     what was actually built, and when
ORIGINALITY.md  what counts as new work for this hackathon
```

## 🛡️ Safety and limits

- Repository code, diffs and model-written tests are treated as untrusted.
  Model-written shell commands are never run on your machine.
- Git is called with argument lists, never through a shell.
- The API key is read from the `MODEL_API_KEY` environment variable and never
  written to logs or reports.

> [!WARNING]
> For now ProofMerge is only meant for the bundled demo project and other
> repositories you own. It makes no safety promise for running untrusted
> external code until isolated runs are built and tested. A PASS means the
> tests that ran found nothing. It does not mean the code is correct or secure.

## 🧰 How it was built

Muse Spark and Muse Wonder are the only models ProofMerge uses at runtime.
AI coding assistants were used as development tools while building it; every
change was reviewed and checked by running the tests. See
[ORIGINALITY.md](ORIGINALITY.md) for what was built during the event window.
