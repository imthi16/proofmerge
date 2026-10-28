# Prior art

ProofMerge does not claim a new technique. Generating tests with an LLM to expose
behavior differences between two versions of a program, and judging those differences
against a pull request's stated intent, already exists in research. This page lists the
closest work and says, for each, what ProofMerge does differently.

Every statement about a paper below is taken from its arXiv abstract, at the link given
(fetched 2026-10-28). Quoted text is verbatim. Anything not confirmed from the abstract is
marked **unverified**.

## Summary

| Work | Inputs | What it produces | Closest ProofMerge part |
|---|---|---|---|
| Testora | a PR, its title, description and commits | regressions, by classifying behavior differences as intended or unintended | intent contract, base/head tests |
| DiffTestGen | old and new version of a program, PRs | tests that expose behavior differences | Spark hypotheses and probes |
| Mokav | two program versions and an example input | a difference-exposing test input | base/head differential runs |
| PatchDiff | two patches for the same issue | tests exposing behavioral discrepancies between patches | differential runs; benchmark realism |
| TestGen-LLM (Meta) | existing human-written tests | improved test classes that pass assurance filters | probe validation: a probe counts only if it passes on base |
| ACH (Meta) | code and an issue of concern | mutants and tests that kill them | PASS strength (stretch) |

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
- **How ProofMerge differs:** Testora is the closest prior work, and the idea of using the
  PR's natural-language intent as the oracle is Testora's. In Testora an LLM classifies
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

## What ProofMerge adds

No single part below is claimed as new. The contribution is the combination, built for
AI-agent pull requests:

1. An explicit intent contract whose claims must quote the PR text exactly, checked by
   code and shown in the report.
2. A deterministic test-tamper check, aimed at agents that make a suite green by editing
   its tests.
3. A deterministic verdict that says REVIEW instead of guessing, where a model can only
   lower confidence.
4. A replayable evidence bundle: commands, exit codes, commit SHAs and file hashes.
5. Real benchmark numbers against simple baselines.

## Not covered here

- Commercial review tools (for example CodeRabbit, Greptile, Qodo Cover, Copilot code
  review) are not compared on this page. Their features were not checked from primary
  sources, so no claims about them are made. **Unverified.**
- Peer-reviewed venues for these papers are not listed, because the arXiv abstracts do not
  state them. **Unverified.**
- No search was done for prior tools that detect test tampering by AI agents. Item 2 above
  is therefore not claimed as new.
