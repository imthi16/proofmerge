# ProofMerge — 10-day Solo Sprint

**Hackathon:** Mon Oct 26 through Wed Nov 4, 2026.  
**Submission:** Wed Nov 4 at 9 PM ET = Thu Nov 5 at 7:30 AM IST. Aim to submit earlier.  
**Track/theme:** Indie Developer / Push the Frontier.

Everything here is **planned**, not necessarily implemented. Mark done only after code, tests and commit evidence.

| Date | Build milestone | Acceptance gate |
|---|---|---|
| Oct 26 | Ideation, repo created, project objective | Clear positioning; new-work-only |
| **Oct 27** | Python packaging/CLI; Git diff context; Meta API smoke test | Real authenticated Spark response; parsing tested |
| Oct 28 | Structured falsifiable Spark hypotheses | Given fixture diff → valid hypothesis with executable test intent |
| Oct 29 | Git base/head worktrees + safely isolated test execution | Controlled BASE PASS / HEAD FAIL captured with hashes |
| Oct 30 | Generated test construction + validation + strict budgets | Spark-directed probe runs on both revisions; invalid probes → REVIEW |
| Oct 31 | Evidence normalizer + deterministic verdict + Wonder summarizer | Structured PASS/REVIEW/BLOCK derived from execution |
| Nov 1 | Reproducible proof bundle and strong end-to-end fixture | One-command working end-to-end demo with audited outputs |
| Nov 2 | Benchmark expansion + quality/performance fixes | Publish *actual* bad/safe patch confusion matrix and latency |
| Nov 3 | GitHub integration if stable; README; prepare 3-minute video | Fresh clone follows README; no secrets; demo rehearsed |
| Nov 4 | Freeze; demo recording; incognito repo audit; submission | Submit before cutoff; working URLs and recording |

## Scope gates
- By **Oct 28**: Meta processes a real diff into a falsifiable hypothesis.
- By **Oct 30**: at least one generated adversarial test truly runs in a controlled sandbox against base and head.
- By **Nov 1**: complete fixture → evidence → verdict → proof bundle succeeds.
- After **Nov 1**: correctness, reproducibility, safety, benchmark and demo outrank new integrations.

## Daily execution checklist
1. Inspect `CLAUDE.md`, issue/task, state and latest BUILDLOG.
2. Define one testable deliverable before asking a coding assistant to implement.
3. Write/adjust failing test, implement and run local checks.
4. Inspect diff and evidence; note unsupported scenarios; update BUILDLOG.
5. Commit incremental work; avoid making untested code appear complete.
6. Reassess deadline risk; remove optional scope ruthlessly.

## Working with five AI tools
The **five assistants are development helpers**, not five required runtime model integrations:
- **Claude Code (lead implementer):** architecture, Python code, tests, refactoring.
- **ChatGPT/Codex (skeptical engineer):** independent code review, adversarial edge cases, debugging.
- **Gemini (independent critique):** detect missing experiment assumptions, benchmark leakage, revise docs.
- **OpenCode (if available):** alternate harness for small self-contained tasks; optional, never critical path.
- **Grok (if available):** external pitch/novelty stress test; optional, never critical path.

**Core runtime models:** Meta Muse Spark + Muse Wonder. Avoid provider sprawl.

## Short demo narrative
"AI agents produce PRs rapidly. Existing tests pass. A second model saying 'LGTM' isn't proof. ProofMerge asks Muse Spark to find an executable counterexample, runs it independently on both revisions, then blocks only when reproducible evidence confirms a regression."

Judging according to kickoff: Impact 30%; Meta-tool use 25%; Technical depth 20%; demo 25%. The path to points is meaningful autonomy + execution evidence + real metrics + a crisp demonstration, not flashy UI.
