# Verify a pull request with ProofMerge

Use this only for synthetic, owned repositories. Docker is required; use a Linux
runner with Docker available. ProofMerge makes no safety claim for arbitrary code.
Verification uses the Meta API and consumes credits when a key is available.

Copy this workflow into the repository you want to verify:

```yaml
name: ProofMerge
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: imthi16/proofmerge@main
        with:
          model-api-key: ${{ secrets.MODEL_API_KEY }}
          fail-on: block
          python-version: '3.13'
```

Set the repository secret `MODEL_API_KEY` to your Meta API key. The default
`fail-on: block` fails on BLOCK; `fail-on: review` also fails on REVIEW. PASS means
no regression found within the tested scope. Missing or malformed evidence,
unexpected verify exit codes, invalid policy input, and setup errors fail the job.
A no-change verification currently produces no evidence and therefore fails.

Fork pull requests receive no secret: an empty key prints
`skipped: no MODEL_API_KEY (fork PR?)`, emits a warning, writes
`proofmerge-evidence/skipped.json` (`status: skipped`, `verdict: null`), and
succeeds without installing, building, verifying, or commenting. Set
`fail-on-missing-key: true` to fail the job after that record is written. The
default `false` matches the previous skip-and-succeed behavior. Do not switch
to `pull_request_target` to expose secrets to fork code.

The action does not accept a Python import path or an install flag. A
`pull_request` workflow file can be changed by a same-repository pull request,
so those settings come only from the base commit's `.proofmerge.toml`. See
[python-projects.md](python-projects.md).

The action installs its own code and builds its own runner image, independent of
the checked-out PR. It compares the event's exact base and head SHAs and passes
the PR body as a file. Evidence is uploaded even after verification failures;
`proof.json`, `report.html`, and the manifest are in `proofmerge-evidence` when
verification completes. The PR comment includes verdict, reasons, and the artifact
link. Subsequent runs update the bot-authored comment marked
`<!-- proofmerge -->`; other authors' comments are left alone. Creation uses
`gh pr comment --body-file`; updates use `gh api --input` with a JSON file to target
the exact marked comment. PR text and evidence strings are never shell code.
Reasons are escaped as literal text to avoid active Markdown or mentions.

Keep one verification job per PR and serialize overlapping runs if needed to avoid
racing comment creation. The runner needs `gh`, Git, Docker, and access to the PR
SHAs. Artifact and comment publication require GitHub access; verification requires
Meta API access. The action's real GitHub execution still needs to be validated.
