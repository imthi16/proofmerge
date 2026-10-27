# ProofMerge — Product & Engineering Specification
Status: planning contract (not a claim of implemented functionality)  
Created for the Oct 26–Nov 4, 2026 Meta Global AI Developer Hackathon.

## Product thesis
AI coding agents can draft patches faster than developers can independently establish their safety. A second AI saying "looks good" is an unverified opinion. ProofMerge actively searches for a **falsifiable counterexample**, executes experiments against **base and patched revisions**, and reports **reproducible evidence**.

**Not** formal verification. "Proof bundle" is an auditable set of *bounded executable observations*, not a guarantee that code is correct or secure.

## User and core use case
Primary persona: a developer or maintainer reviewing an AI-generated pull request in an authorized repository.

Command-line acceptance target:
```bash
proofmerge verify ./demo/demo-shop --base main --head candidate/tenant-cache-regression
```
A successful experiment illustrates:
```text
Existing test suite: green
Spark hypothesis: tenant ID may be omitted from cache key
Generated test:
  BASE: PASS
  HEAD: FAIL
Verified counterexample: cross-tenant cache collision
Recommendation: BLOCK
Reproduce: pytest <recorded_generated_test> -q
```
Use a clean fixture and *actual* execution to demonstrate this. Never fabricate model output, times, test counts or benchmarks.

## Runtime architecture
```text
Repository / PR adapter
  → diff + repo context builder
  → Meta Muse Spark 1.3, Prosecutor Agent
  → structured falsifiable hypotheses + test candidates
  → verifier orchestration
     ├─ base worktree → controlled isolated runner
     └─ head worktree → controlled isolated runner
  → normalized execution evidence
  → deterministic regression / failure classifier
  → Meta Muse Wonder 1.0, independent evidence summarizer
  → final policy engine (deterministic precedence)
  → report.md + proof.json + artifact manifest
```

Spark can explore the relevant files, inspect call sites, generate *small* targeted probes, and adapt failed experiments within configured tool-step budgets. Wonder receives diff and **recorded evidence**, NOT Spark's verdict; its summary must not falsify or overrule test outcomes.

Meta API: `https://api.meta.ai/v1`, authenticated via `MODEL_API_KEY`. Initial models: `muse-spark-1.3`, `muse-wonder-1.0`; verify currently exposed model IDs in the developer account before coding. Prefer structured JSON output and tool calling. All runtime Meta keys come from the environment.

## Interfaces and initial data contract
Suggested schemas; evolve with tests, and document changes:

```text
Hypothesis:
  id, title, changed_locations, suspected_behavior,
  expected_base, expected_head, test_intent,
  risk_category, falsification_criteria

Experiment:
  hypothesis_id, test_source_path, test_source_sha256,
  command_argv, timeout_seconds, environment_profile

Execution:
  revision, commit_sha, runner_image_digest,
  exit_code, stdout_path, stderr_path, duration_ms, outcome

Finding:
  hypothesis_id, base_execution, head_execution,
  classification, reproducer_command, verified

Verdict:
  decision: PASS | REVIEW | BLOCK,
  verified_findings, unresolved_risks, evidence_artifact_paths,
  scope_limitations, model_usage
```

Keep source files, commands, logs and hashes in `evidence/<run-id>/` (ignored locally when appropriate). Track base/head SHAs and diff hash for provenance. Hashes detect file modification after capture; **do not** imply signed or tamper-proof provenance unless signatures exist.

## Differential-result classification
| Base | Head | Interpretation |
|---|---|---|
| PASS | FAIL | Candidate introduced observed regression → BLOCK when repeatable and valid |
| FAIL | PASS | Possible bug fix; not a new regression |
| FAIL | FAIL | Existing/unresolved failure; do not assert regression |
| PASS | PASS | No failure found by this probe; not general proof |
| ERROR / TIMEOUT / FLAKY | any | REVIEW until conclusively resolved |

Test validity needs scrutiny: generated test must exercise the *same intended behavior* against both revisions. Avoid false positives from nondeterminism, fixture leakage, environmental differences and patch-specific assertions. Rerun suspected regressions where practical.

## Security boundaries
Generated tests and external repository code are untrusted. **Do not use unrestricted subprocess execution on the host as a general product feature.** Start only with synthetic, developer-owned test fixtures; isolate base/head runs in separate, ephemeral, nonprivileged environments before expanding input surface.
Target hardened container parameters: disabled outbound network; restricted capabilities; no host Docker socket; no privileged mode; resource/CPU/RAM/PID/time limits; no runtime API keys inside test containers; read-only image and narrowly scoped writable scratch; no arbitrary user-controlled mounts. Handle Docker unavailable → REVIEW, not unsafe fallback.

Do not scan unauthorized repositories or perform active attacks. This project is defensive software quality verification.

## Decision policy
- BLOCK only on confirmed, reproducible patch regression or clearly enforced hard rule.
- REVIEW on agent error, failed test setup, insufficient test coverage, ambiguous hypothesis, flaky result, unverified high-risk concern, or missing sandbox.
- PASS only if required, configured checks actually ran and passed. Word as "No verified regression found in executed checks" and show coverage limitations.
- The LLM never directly controls the final decision. Stable deterministic code takes precedence.

## Benchmark and demo design
Create `demo/demo-shop` (small, own work) with logic around inventory, pricing, webhooks, caching and authorization. Build controlled, labeled candidate patches. Initial aim: 12 faulty + 8 safe = 20 cases, **stretch target**, not an achieved result.
Measure actual:
- faulty patch recall / detected regressions,
- safe patch false-block rate,
- proportion of claimed findings with reproducible evidence,
- verifier timeout and flaky-test rate,
- wall-clock p50/p95 latency,
- Meta tokens and cost per verification.

Maintain a held-out set to reduce tailoring results to known answers. Show failures, not just successful examples.

## Three-minute judge demo
1. Show an AI-made-looking patch whose existing suite passes.
2. Run ProofMerge on that PR/ref pair.
3. Show Spark's falsifiable failure hypothesis.
4. Show a generated experiment run on both commits: BASE PASS → HEAD FAIL.
5. Show deterministic BLOCK, evidence file and one-line reproduction.
6. End on **measured actual benchmark stats**, not estimated metrics.

## Out of scope during the sprint
General code review chatter, elaborate UI, SaaS users, billing, multi-tenant hosting, auto-merging, full language ecosystem, broad Internet execution, custom local 30B serving, and invented formal guarantees.

## References
- Meta Model API: https://dev.meta.ai/docs/quickstart
- Meta GitHub agent cookbook (read and differentiate): https://dev.meta.ai/docs/cookbook/github-agent
- Meta structured output: https://dev.meta.ai/docs/structured-output
- Event: https://cerebralvalley.ai/e/meta-ai-developer-hackathon

## Next engineering action
Before complex prompts or UI, write one deterministic integration fixture that exhibits **base PASS / head FAIL**, using a controlled executor. Validate this manually and in an automated test. Then attach the Meta model.
