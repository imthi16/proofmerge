---
name: proofmerge
description: Check your own code change with ProofMerge before you call it done. Use after you finish a change on a git branch, before you say the task is complete, open a pull request or ask for a merge. ProofMerge looks for regressions the diff introduced, runs targeted probes on base and head in Docker and returns PASS, REVIEW or BLOCK with replayable evidence.
---

# Verify a change with ProofMerge

ProofMerge compares two committed revisions of a git repository. It writes
probes aimed at what the change could break, runs each probe on base and head in
Docker, and decides the verdict in code: BLOCK only when a probe passes on base
3 of 3 times and fails on head 3 of 3 times with an assertion failure. Its
verdict is evidence, not an opinion, so report it as it is.

## Before you run it

- `proofmerge --help` works. If not, tell the user to install it:
  `uv tool install git+https://github.com/imthi16/proofmerge`, then
  `proofmerge setup`.
- `MODEL_API_KEY` is set in the environment. Never ask the user to paste it into
  the chat, never print it and never write it to a file.
- The repository is one the user owns or trusts. ProofMerge runs the
  repository's tests in Docker; its isolation is not hardened for arbitrary
  third-party code.
- One run makes at most 19 Meta model calls. Ask before running it more than
  twice for the same task.

## Run it

1. Commit the change on a branch. ProofMerge only sees committed revisions, so
   uncommitted edits are not checked.
2. Write the intent to a file outside the repository: what should change and
   what must stay the same, taken from the user's request or the PR description.
   Write it before you see a verdict and never edit it afterwards to change one.
   A sentence that claims a change counts in the verdict, so do not add one to
   excuse a difference the user did not ask for.
3. Run, with a new evidence directory each time:

   ```bash
   proofmerge verify <repo> --base <base-ref> --head <branch> \
     --intent-file <intent.md> --evidence-dir <new-directory>
   ```

4. Read the exit code: 0 is PASS or REVIEW (the output says which), 2 is BLOCK,
   1 means ProofMerge could not run.

## Act on the verdict

- **BLOCK**: a verified regression. Open `<evidence-dir>/report.html`, read the
  failing hypothesis card, its probe source and "Why head failed". Fix the code,
  commit, and run `verify` again with a new evidence directory. After two fixes
  that still end in BLOCK, stop and hand it to the user.
- **REVIEW**: not conclusive. Do not call the change verified or passing. Give
  the user the reasons from the output, word for word, and the report path.
- **PASS**: say "ProofMerge found no regression within the tested scope". Never
  say the change is proven correct or safe.
- **Exit 1**: report the error message. Do not retry in a loop and do not report
  the task as verified.

## Never

- Edit, skip or delete tests or ProofMerge probes to change a verdict. The
  test-tamper guard checks for exactly this.
- Edit files in the evidence directory. `manifest.json` hashes them and
  `proofmerge replay` reports any change.
- Claim a verdict you did not get from a run in this session.

## Report back

Tell the user, in this order:

1. The verdict and each reason line from the output.
2. The report: `<evidence-dir>/report.html`.
3. How to check the evidence: `proofmerge replay <evidence-dir>/proof.json`
   (add `--rerun --repo <repo>` to run the probes again in Docker).
