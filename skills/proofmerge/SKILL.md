---
name: proofmerge
description: Verify your own committed code change with ProofMerge before you call it done. Use after you finish a change on a git branch and before you say the task is complete, open a pull request or ask for a merge; also when the user asks you to check, prove or verify a branch or pull request with ProofMerge. ProofMerge writes probes aimed at what the change could break, runs them on base and head in Docker, and returns PASS, REVIEW or BLOCK with replayable evidence. Not for uncommitted edits, repositories the user does not own or trust, or code reviews that do not need a run.
---

# Verify a change with ProofMerge

ProofMerge compares two committed revisions of a git repository. It writes
probes aimed at what the change could break, runs each probe on base and head
in Docker, and decides the verdict in code: BLOCK only when a probe passes on
base 3 of 3 times and fails on head 3 of 3 times. A model can move a verdict
toward REVIEW, never to PASS or BLOCK. The verdict is evidence, not an opinion:
your job is to run it honestly and report it exactly.

## Rules that always apply

- NEVER edit, skip, weaken or delete tests or ProofMerge probes to change a
  verdict. The test-tamper guard runs the original tests against the change and
  catches exactly this.
- NEVER edit files in an evidence directory. `manifest.json` hashes them, and
  `proofmerge replay` recomputes the verdict from the recorded runs, so a
  rewritten verdict fails even with a fresh manifest.
- NEVER claim a verdict you did not get from a run in this session, and never
  call a REVIEW "passed" or a PASS "proven correct".
- NEVER ask the user to paste `MODEL_API_KEY` into the chat, print it or write
  it to a file. It must already be in your environment.
- ASK the user before you run `verify` more than twice for the same task: one
  run makes at most 19 Meta model calls.
- ASK the user before you run it on a repository they do not own or trust:
  ProofMerge runs the repository's tests in Docker, and its isolation is not
  hardened for arbitrary third-party code.

## 1. Check that it can run

Run these once per session and stop at the first failure:

1. `proofmerge version` prints a version. If the command is missing, tell the
   user to install it with
   `uv tool install git+https://github.com/imthi16/proofmerge`, then stop.
2. `MODEL_API_KEY` is set in your environment (check that it is non-empty
   without printing it). If it is missing, tell the user to set it in their
   shell or on the Setup page of `proofmerge app`, then stop.
3. Docker is running and the runner image exists. If `verify` reports a missing
   image, run `proofmerge setup` once; it builds the image.

## 2. Prepare the run

1. Commit the change on its own branch. ProofMerge only sees committed
   revisions; uncommitted edits are not checked.
2. Pick the base: the branch the change will merge into (often `main`).
3. Write the intent to a file **outside** the repository, before you see any
   verdict, and never edit it afterwards. Take it from the user's request or the
   pull request description, in two parts:
   - what should change, in the user's words;
   - what must stay the same.

   A sentence that claims a change counts in the verdict. Do not add one to
   excuse a difference the user did not ask for.

## 3. Run it

Use a new evidence directory every time:

```bash
proofmerge verify <repo> --base <base-ref> --head <branch> \
  --intent-file <intent.md> --evidence-dir <new-directory>
```

Read the exit code: 0 is PASS or REVIEW (the output's `verdict:` line says
which), 2 is BLOCK, 1 means ProofMerge could not run.

## 4. Act on the verdict

| Verdict | What you do |
|---|---|
| **BLOCK** | A verified regression. Open `<evidence-dir>/report.html` and read the failing hypothesis card: its probe source and "Why head failed". Fix the code, commit, and run `verify` again with a new evidence directory. After two fixes that still end in BLOCK, stop and hand it to the user. |
| **REVIEW** | Not conclusive. Do not call the change verified or passing. Give the user the reasons from the output, word for word, and the report path. |
| **PASS** | Say "ProofMerge found no regression within the tested scope". Never say the change is proven correct or safe. |
| **Exit 1** | Report the error message as printed. Do not retry in a loop and do not report the task as verified. |

## 5. Report back

Tell the user, in this order and in this shape:

```text
ProofMerge verdict: BLOCK
- H1 is a verified regression against uncontested PRESERVE claim C0 with intent present.
- H2 needs review because its probe may not test its hypothesis.
Report: <evidence-dir>/report.html
Check the evidence: proofmerge replay <evidence-dir>/proof.json
```

Copy the verdict and every reason line exactly as `verify` printed them; the
lines above are from a real run and only show the shape. Add
`--rerun --repo <repo>` to the replay command when the user wants the probes run
again in Docker. If you fixed code after a BLOCK, report each run's verdict in
order, not only the last one.
