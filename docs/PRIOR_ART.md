# Prior art

ProofMerge does not claim a new technique. Generating tests with an LLM to expose
behavior differences between two versions of a program, and judging those differences
against a pull request's stated intent, already exists in research. This page lists the
closest work and says, for each, what ProofMerge does differently.

Every statement about a paper below is taken from its arXiv abstract, at the link given
(fetched 2026-10-28). The one exception is ORION, which was read in full (v1 PDF). Quoted text is verbatim. Anything not confirmed from the abstract is
marked **unverified**.

## Summary

| Work | Inputs | What it produces | Closest ProofMerge part |
|---|---|---|---|
| ORION | a PR, its intent, the changed code | PR-level regression classification from oracles of preserved and changed behavior | intent contract, base/head probes, verdict rules |
| Testora | a PR, its title, description and commits | regressions, by classifying behavior differences as intended or unintended | intent contract, base/head tests |
| PatchGuru | a PR and its natural-language artifacts | runtime assertions over pre- and post-patch versions | intent contract, probes |
| ChaCo | a PR and its uncovered lines | tests that raise patch coverage | none: assumes the PR is correct |
| Fault-enforcing study | LLM-generated regression tests for merged PRs | how often those tests encode the bug | probe validity rule |
| DiffTestGen | old and new version of a program, PRs | tests that expose behavior differences | Spark hypotheses and probes |
| Mokav | two program versions and an example input | a difference-exposing test input | base/head differential runs |
| PatchDiff | two patches for the same issue | tests exposing behavioral discrepancies between patches | differential runs; benchmark realism |
| TestGen-LLM (Meta) | existing human-written tests | improved test classes that pass assurance filters | probe validation: a probe counts only if it passes on base |
| ACH (Meta) | code and an issue of concern | mutants and tests that kill them | PASS strength (stretch) |

## ORION

- **Paper:** "Behavioral Regression Detection for Pull Requests via Structured Oracles",
  Yiwen Fu, Binhang Qi, Xiao Li, Runlin Liu, Hailong Sun, Xiang Gao, arXiv 2610.35223,
  first submitted 2026-10-26. <https://arxiv.org/abs/2610.35223>. Read in full (v1 PDF).
  Code: <https://github.com/wenwen-666/ORION> (the paper says "Our source code and
  experimental data are available at" this link; the GitHub API shows no license file).
- **What it does:** four stages. It extracts the PR's intent from the title, description
  and diff summary into four fields (change type, expected behavior change, backward
  compatibility, high-level expectations), and finds the changed functions by AST
  parsing. It then writes per-function oracles of preserved and changed behavior,
  generates tests from them, and runs the same tests in matching Conda/Docker
  environments for the pre- and post-change code. A static repair pass fixes imports and
  syntax only: "ORION deliberately avoids semantic adaptation of tests to either the pre-
  or post-change versions of the program."
- **How it decides:** "Regression decisions follow a deterministic-first strategy." Hard
  rules handle clear cases: identical runs are clean; infrastructure failures are not
  evidence; a slowdown of at least twice the runtime is a regression. For the rest, "The
  LLM does not freely judge regressions from raw outputs; instead, it answers five
  standardized yes/no questions". A test case is a regression when Q1 (there is an
  observable difference) is yes and any of Q2 to Q5 (the difference violates preserved
  behavior, or the slowdown is not intended) is yes. A PR is regression-inducing if any
  case is. "Infrastructure-failure cases do not themselves trigger a regression, and PRs
  containing only such cases are additionally flagged for human review."
- **Reported results:** it introduces "RegBench-Py, the first Python PR regression
  benchmark with 154 verified real-world PRs" (77 regression and 77 clean PRs), reports
  "F1-scores of 0.6125 (DeepSeek-Chat-V3.2) and 0.5578 (Claude-4.5-haiku), outperforming
  baselines including Testora, Codex and Claude Code", and "On 1,459 merged PRs, ORION
  finds 9 reproducible regression candidates, 8 confirmed by maintainers". "It takes ~15
  minutes per PR". Across three DeepSeek runs, "the standard deviations of Precision,
  Recall, and F1-score are 0.040, 0.057, and 0.048". Its threats section says "It may
  miss cases involving complex states, remote services, caches, asynchronous behavior, or
  optional dependencies".
- **How ProofMerge differs:** ORION is the closest work found. Like ProofMerge, it
  separates preserved from changed behavior, runs identical tests on both revisions, and
  applies deterministic rules before any model. ORION was posted on 2026-10-26; this page
  added it on 2026-10-28. The differences, as far as the paper describes ORION, are
  ProofMerge's design. Status on 2026-10-29: all six are on `main`; each item names where
  it is built and what is still planned.
  1. **Who can call a regression.** In ORION, an LLM's yes/no answers can label a case a
     regression. In ProofMerge, BLOCK needs a probe that passes on base and fails on head
     with an assertion failure, 3 runs out of 3. A model can only move a verdict toward
     REVIEW. *Built: `src/proofmerge/verdict.py`, wired end to end by `proofmerge verify`.*
  2. **What counts as a difference.** ORION compares captured outputs: status,
     exceptions, return values, stdout, runtime. A ProofMerge probe states its expected
     base result as an assertion, so a head run either passes or fails it. Performance is
     out of scope. *Built: `src/proofmerge/probes.py` (a probe must pass on base before head
     runs).*
  3. **Intent grounding.** ORION normalizes the intent into four fields. The paper does
     not describe checking them against the PR text. Each ProofMerge claim must quote the
     PR text exactly, which code checks. *Built: `src/proofmerge/intent.py`.*
  4. **Repeated runs and replay.** The paper reports run-to-run variation across whole
     benchmark runs. It does not describe re-running each test to detect flakiness, or a
     hashed, replayable evidence bundle. *Built: 3 attempts per revision
     (`src/proofmerge/sandbox.py`) and a hashed manifest checked by `proofmerge replay`
     (`src/proofmerge/manifest.py`); `proofmerge replay --rerun` re-runs the recorded
     experiments in Docker and reports whether each outcome reproduces.*
  5. **Test tampering.** The paper does not discuss a PR that edits or deletes its own
     tests. *Built: a static check and the base version of each changed test run on head
     (`src/proofmerge/tamper.py`).*
  6. **Three verdicts.** ORION labels a PR regression or clean, and flags only
     infrastructure-only PRs for review. ProofMerge returns REVIEW whenever the evidence
     is not conclusive. *Built: `src/proofmerge/verdict.py`.*
  ProofMerge has no benchmark at ORION's scale. RegBench-Py is said to be in the ORION
  repository; that was not checked.

## Testora

- **Paper:** "Testora: Using Natural Language Intent to Detect Behavioral Regressions",
  Michael Pradel, arXiv 2503.18597, first submitted 2025-03-24.
  <https://arxiv.org/abs/2503.18597>
- **What it does:** "Given a pull request (PR), Testora queries an LLM to generate tests that
  exercise the modified code, compares the behavior of the original and modified code, and
  classifies any behavioral differences as intended or unintended." The classification is
  "an LLM-based technique that leverages the natural language information associated with
  the PR, such as the title, description, and commit messages".
- **Reported results:** on Python projects it found "19 regression bugs"; "Out of 13
  regressions reported to the developers, 11 have been confirmed and 9 have already been
  fixed", at "12.3 minutes to check a PR and LLM costs of only $0.003 per PR".
- **How ProofMerge differs:** the idea of using the PR's natural-language intent as the
  oracle is Testora's. In Testora an LLM classifies
  each difference as intended or unintended. In ProofMerge the model only proposes claims.
  Each claim must quote the intent text exactly, which deterministic code checks. The
  verdict then comes from fixed rules over the execution results, and a model can only
  move it toward REVIEW, never to BLOCK or PASS.

## DiffTestGen

- **Paper:** "DiffTestGen: Change-Directed LLM-Based Testing for Exposing Behavioral
  Differences", Huimin Hu, Cristian Cadar, Michael Pradel, arXiv 2607.16024, first
  submitted 2026-07-17. <https://arxiv.org/abs/2607.16024>
- **What it does:** a "change-directed, LLM-based differential testing approach
  specifically designed to expose behavioral differences introduced by a code change". It
  "leverages static call graph analysis and project documentation to identify valid entry
  points" and iterates on a "union coverage metric, which combines coverage of modified
  code in the old and the new version".
- **Reported results:** on "463 PRs", it "exposes behavioral differences in 78.2% of the
  PRs while achieving an average union coverage of 90.7%". Combined with Testora, the
  differences "can be used to detect regression bugs missed by the best existing
  approaches".
- **How ProofMerge differs:** DiffTestGen is about finding behavior differences, with high
  coverage of the changed code. ProofMerge has a narrower goal. It ties each hypothesis to
  one intent claim and stops at an explainable merge recommendation with a replayable
  evidence bundle. It does not use call graphs or coverage feedback, so it will reach less
  of the changed code than DiffTestGen.

## PatchGuru

- **Paper:** "PatchGuru: Patch Oracle Inference from Natural Language Artifacts", Thanh
  Le-Cong, Bach Le, Toby Murray, Michael Pradel, Cristian Cadar, arXiv 2602.05270, first
  submitted 2026-02-05. <https://arxiv.org/abs/2602.05270>
- **What it does:** "uses large language models (LLMs) to extract developer intent from NL
  artifacts and synthesizes patch oracles: under-approximate yet practical specifications
  expressed as runtime assertions in comparison programs that integrate pre- and
  post-patch versions".
- **Reported results:** on "400 recent PRs" it "reports 39 warnings with a precision of
  0.62, yielding 24 confirmed true positives"; against Testora it "detects 17 more bugs
  (24 vs. 7)", at "8.9 minutes and USD 0.07 per PR".
- **How ProofMerge differs:** PatchGuru's warning is a violated LLM-written assertion
  inside one program that calls both versions. ProofMerge runs the same probe as an
  ordinary test in two separate checkouts, and counts it only when it passes on base and
  fails on head.

## Change And Cover (ChaCo)

- **Paper:** "Change And Cover: Last-Mile, Pull Request-Based Regression Test
  Augmentation", Zitong Zhou, Matteo Paltenghi, Miryung Kim, Michael Pradel, arXiv
  2601.10942, first submitted 2026-01-16. <https://arxiv.org/abs/2601.10942>
- **What it does:** "an LLM-based test augmentation technique" for the gap where "some
  PR-modified lines remain untested". It "carefully integrates them into the existing test
  suite".
- **Reported results:** on "145 PRs" it "helps 30% of PRs achieve full patch coverage, at
  the cost of $0.11".
- **How ProofMerge differs:** ChaCo adds tests for code it takes as correct. ProofMerge
  asks whether the PR itself broke something, and does not add tests to the repository.

## Risks of LLM-generated regression tests

- **Paper:** "On the Risks of using LLM-Generated Tests for Regression Testing", Mohammadali
  Charoosaei, Cedric Richter, Mike Papadakis, arXiv 2610.11835, first submitted
  2026-10-08. <https://arxiv.org/abs/2610.11835>
- **What it finds:** "Across 145 pull requests from SciPy, Qiskit, and pandas, 8%-17% of
  the generated tests are fault-enforcing, while only 2.4%-4.8% reveal faults". A
  fault-enforcing test is one that asserts "faulty behavior".
- **Why it matters here:** a test written from the head code can lock in the bug. This is
  why a ProofMerge probe must pass on the base revision before a head failure counts: a
  fault-enforcing probe fails on base, so it is never evidence for BLOCK.

## Mokav

- **Paper:** "Mokav: Execution-driven Differential Testing with LLMs", Khashayar Etemadi,
  Bardia Mohammadi, Zhendong Su, Martin Monperrus, arXiv 2406.10375, first submitted
  2024-06-14. <https://arxiv.org/abs/2406.10375>
- **What it does:** "Mokav takes two versions of a program (P and Q) and an example test
  input" and generates a difference exposing test (DET), "a test input that leads to
  provably different outputs on P and Q". It "iteratively prompts an LLM" with
  "execution-based feedback from previously generated tests".
- **Reported results:** DETs for "81.7% (1,255/1535) of the program pairs" from Codeforces,
  "versus 4.9% for Pynguin and 37.3% for Differential Prompting".
- **How ProofMerge differs:** Mokav finds any input on which two programs differ, on
  standalone program pairs. ProofMerge works on a repository pull request. It treats a
  difference as a regression only when it breaks a PRESERVE claim, and it treats a
  difference the PR intends as acceptable.

## PatchDiff

- **Paper:** *Are "Solved Issues" in SWE-bench Really Solved Correctly? An Empirical Study*,
  You Wang, Michael Pradel, Zhongxin Liu, arXiv 2503.15223, first submitted 2025-03-19.
  <https://arxiv.org/abs/2503.15223>
- **What it does:** introduces "PatchDiff for differential patch testing, which
  automatically exposes behavioral discrepancies between two patches", used to compare
  agent-generated patches with human ground-truth patches on SWE-bench Verified.
- **Reported results:** "7.8% of all patches" count as correct while "failing the
  developer-written test suite", and "29.6%" of plausible patches "induce different behavior
  than the ground truth patches". Of the divergent patches inspected by hand, "28.6% ... are
  certainly incorrect".
- **How ProofMerge differs:** PatchDiff compares a generated patch with a known-correct
  patch, which ProofMerge never has. ProofMerge compares the patch with the code before it,
  against the stated intent. PatchDiff's results are the main motivation for ProofMerge: a
  passing test suite does not mean an AI-written patch is correct.

## TestGen-LLM (Meta)

- **Paper:** "Automated Unit Test Improvement using Large Language Models at Meta", Nadia
  Alshahwan, Jubin Chheda, Anastasia Finegenova, Beliz Gokkaya, Mark Harman, Inna Harper,
  Alexandru Marginean, Shubho Sengupta, Eddy Wang, arXiv 2402.09171, first submitted
  2024-02-14. <https://arxiv.org/abs/2402.09171>
- **What it does:** "uses LLMs to automatically improve existing human-written tests" and
  "verifies that its generated test classes successfully clear a set of filters that assure
  measurable improvement over the original test suite, thereby eliminating problems due to
  LLM hallucination".
- **Reported results:** "75% of TestGen-LLM's test cases built correctly, 57% passed
  reliably, and 25% increased coverage"; "73% of its recommendations being accepted for
  production deployment".
- **How ProofMerge differs:** TestGen-LLM improves a test suite. ProofMerge judges one
  patch. ProofMerge borrows its assurance idea: a model-written probe is only evidence after
  deterministic checks, for example it must pass on the base revision before a head failure
  counts.

## ACH (Meta)

- **Paper:** "Mutation-Guided LLM-based Test Generation at Meta", Christopher Foster,
  Abhishek Gulati, Mark Harman, Inna Harper, Ke Mao, Jillian Ritchey, Hervé Robert, Shubho
  Sengupta, arXiv 2501.12862, first submitted 2025-01-22. <https://arxiv.org/abs/2501.12862>
- **What it does:** "generating currently undetected faults that are specific to an issue
  of concern" and then tests "that can catch them, thereby 'killing' the mutants".
- **Reported results:** "applied to 10,795 Android Kotlin classes in 7 software platforms",
  generating "9,095 mutants and 571 privacy-hardening test cases"; "engineers accepted 73%
  of its tests, judging 36% to privacy relevant".
- **How ProofMerge differs:** ACH hardens a code base against a class of faults ahead of
  time. ProofMerge checks one incoming patch. ProofMerge's planned PASS strength (a stretch
  goal) follows ACH's lineage: mutate the changed lines and report how many mutants the
  probes catch, as measured sensitivity, not as proof of correctness.

## Open-source tools

Facts in this table come from each repository's GitHub API record (description, license,
last push), read on 2026-10-28. The tools were not cloned or run, so how they work beyond
the description is **unverified**.

| Repository | License | Last push | GitHub description | Difference from ProofMerge |
|---|---|---|---|---|
| [wenwen-666/ORION](https://github.com/wenwen-666/ORION) | none in API | 2026-10-26 | none | see ORION above |
| [michaelpradel/Testora](https://github.com/michaelpradel/Testora) | MIT | 2026-03-12 | "LLM-based approach to find regression bugs. It checks the behavioral changes introduced by a pull request against its title, description, etc." | see Testora above |
| [sola-st/DiffTestGen](https://github.com/sola-st/DiffTestGen) | MIT | 2026-07-03 | none | see DiffTestGen above |
| [thanhlecongg/PatchGuru](https://github.com/thanhlecongg/PatchGuru) | Apache-2.0 | 2026-06-30 | "LLM-enabled Patch Oracles Inference for Just-in-time Bug Detection" | see PatchGuru above |
| [UCLA-SEAL/Change-Cover](https://github.com/UCLA-SEAL/Change-Cover) | MIT | 2026-01-16 | the ChaCo paper title | see ChaCo above |
| [tuzhechen2005/DiffWitness](https://github.com/tuzhechen2005/DiffWitness) | Apache-2.0 | 2026-10-24 | "A local Gemma 4 model hunts for inputs where your refactor changed behavior; running both versions confirms every finding." | finds any behavior change in a refactor; no intent contract (from the description) |
| [Kakadu525/confidence-scorer](https://github.com/Kakadu525/confidence-scorer) | MIT | 2026-09-24 | Russian; it names differential property-based tests (Hypothesis, fast-check) and an AI reviewer for AI-generated PRs | inputs come from property-based testing, not from intent claims (from the description) |
| [qodo-ai/qodo-cover](https://github.com/qodo-ai/qodo-cover) | AGPL-3.0 | 2026-04-05 | "An AI-Powered Tool for Automated Test Generation and Code Coverage Enhancement" | raises coverage; no base/head comparison is described |
| [The-PR-Agent/pr-agent](https://github.com/The-PR-Agent/pr-agent) | MIT | 2026-10-28 | "PR Agent: The Original Open-Source PR Reviewer." | review comments; no test execution is described |

## Commercial reviewers that run code

Read on 2026-10-30 from each vendor's own page. Neither was tried, so how they work
beyond those pages is **unverified**.

- **CodeRabbit** describes, in its post on
  [context engineering for AI code reviews](https://www.coderabbit.ai/blog/context-engineering-ai-code-reviews),
  running verification in a sandbox as part of its review.
- **Greptile TREX** is presented as
  ["AI code review that runs your code"](https://www.greptile.com/trex).

Running code during review is therefore not what sets ProofMerge apart. What these
pages do not describe, and what ProofMerge is built around, is the combination below:
the same probe run on base and on the patch with repeated attempts, intent claims that
must quote the PR, a test-tamper check, REVIEW instead of a guess, and a hashed evidence
bundle anyone can replay.

## What ProofMerge adds

No single part below is claimed as new. The contribution is the combination, built for
AI-agent pull requests. Status on 2026-10-30: items 1 to 4 are built (hash-checking replay and
`proofmerge replay --rerun`); item 5 includes a [baseline comparison](../bench/results/2026-10-30-baselines/results.json).

1. An explicit intent contract whose claims must quote the PR text exactly, checked by
   code and shown in the report.
2. A deterministic test-tamper check, aimed at agents that make a suite green by editing
   its tests.
3. A deterministic verdict that says REVIEW instead of guessing, where a model can only
   lower confidence.
4. A replayable evidence bundle: commands, exit codes, commit SHAs and file hashes.
5. Real benchmark numbers against simple baselines ([results](../bench/results/2026-10-30-baselines/results.json)).

## Not covered here

- Commercial review tools (for example CodeRabbit, Greptile, QA.tech, Copilot code
  review) are not compared on this page. Their features were not checked from primary
  sources, so no claims about them are made. **Unverified.**
- Peer-reviewed venues for these papers are not listed, because the arXiv abstracts do not
  state them. **Unverified.**
- No search was done for prior tools that detect test tampering by AI agents. Item 2 above
  is therefore not claimed as new.
