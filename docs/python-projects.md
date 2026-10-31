# Python project roots and dependencies

The default Python runner puts `/work/code/src` and `/work/code` on `PYTHONPATH`. That fits a repository whose package lives at the root or under `src/`. A monorepo whose package lives somewhere else, such as `apps/api`, declares that layout in trusted config. Without the declaration the default is unchanged.

This is not the generic language runner from [languages.md](languages.md). A `[python]` table keeps the Python pytest runner, the assertion-versus-other-exception rule, and the test-tamper guard. A top-level `language` key still selects the generic runner and cannot appear in the same file as `[python]`.

## Trust model

ProofMerge reads `.proofmerge.toml` with `git show <base-sha>:.proofmerge.toml`. The base SHA is the commit the pull request is compared against. The copy of the file in the pull request head is ignored, including when the head deletes it, replaces the root, or points `install` at a different lockfile.

Paths are rejected when they are absolute, start with `~`, contain `..`, a backslash, a colon, a NUL, or a component that is not `[A-Za-z0-9_.-]`. Before a probe runs, each import path is checked again on that revision's exported tree: a missing directory, a symlink, or a path that resolves outside the tree is an environment failure.

On a `pull_request` event GitHub uses the workflow file from the merge of the pull request for same-repository PRs, so that file and its action inputs are not a trusted place to name repository paths. The action therefore has no input for a Python root or an install flag. Those settings come only from the base commit.

`proofmerge --config PATH` is the operator override for a local run. The process trusts that path because the operator passed it. The action never passes the flag. If the path is the checkout's `.proofmerge.toml` and those bytes are not the base blob, the command refuses it, so pointing `--config` at a pull request checkout does not silently adopt the head file. A symlink is refused. Put the file outside the checkout when the base branch does not have it yet.

`fail-on-missing-key` is an action input, and a same-repository pull request can change the workflow that sets it. It does not choose code, paths, or packages. The default `false` preserves the previous behavior: an empty key skips verification and the job stays green. It now also prints a warning and writes `proofmerge-evidence/skipped.json` (`status: skipped`, `verdict: null`). `true` fails the job after that record is written. The failure is a missing credential, not a PASS, REVIEW, or BLOCK.

## Config

```toml
[python]
root = "apps/api"          # import path, repository-relative. "." is the repo root.
pythonpath = ["libs/shared"]  # extra import paths, optional
install = false            # default. true installs third-party packages
lockfile = "apps/api/uv.lock"       # used when install is true; default is <root>/uv.lock
pyproject = "apps/api/pyproject.toml"  # optional cross-check against the lockfile
# requirements = "requirements.txt"  # alternative to uv.lock; every line must be name==version with sha256 hashes
python_version = "3.13"    # runner image, used only to evaluate lockfile markers
```

`root` omitted and `pythonpath` empty keeps `PYTHONPATH=/work/code/src:/work/code`.

## Dependency install

`install = false` is the default. Nothing is downloaded, and probe containers stay on `--network none`.

`install = true` reads the lockfile or requirements file from the **base** commit, not from the head tree and not by executing project code. A `uv.lock` is reduced to `name==version` lines with the sha256 of every published wheel. The project itself is not installed; the exported tree stays on `PYTHONPATH`. Path, git, and URL packages are refused. Packages with no wheel are refused, so an sdist cannot run `setup.py`. Marker expressions are evaluated for CPython on Linux at `python_version`, using this machine's CPU architecture because a native container shares it. An unknown marker variable fails the install.

The install runs in its own container: network is `bridge` (so the index in the lockfile can be reached), capabilities are dropped, the host environment is not passed (`MODEL_API_KEY` is not in the container), the root filesystem is read-only, and the dependency directory is the only writable mount. `pip install` is invoked with `--require-hashes`, `--only-binary=:all:`, and `--no-deps`. Probe containers then mount that directory read-only at `/deps` and still use `--network none`.

If head's lockfile differs from base, the proof records that head's dependency file was not installed. A new import that needs a package only the pull request added fails as an environment error.

## What an import error means

A pytest assertion failure (exit 1) is a behavioral FAIL. `ModuleNotFoundError` or `ImportError` on any other exit, a missing import path, or a failed install is recorded as `setup_failure` and classified `ENVIRONMENT`. The verdict is REVIEW. It is not PASS and it is not BLOCK, including when every attempt on both revisions fails the same way.

## HelpDeck

HelpDeck's API package lives at `apps/api` and is installed with uv (`apps/api/pyproject.toml`, `apps/api/uv.lock`). `app.agent.prompts` is standard-library only. `retrieval`, `graph`, and `chunker` import third-party packages.

To adopt this on HelpDeck:

1. Land `.proofmerge.toml` on **main** in its own pull request (not on the regression branch). ProofMerge reads the file from the base SHA, so a pull request does not pick the file up until that SHA contains it. Re-run the workflow after main moves so `pull_request.base.sha` includes the new commit.

   ```toml
   [python]
   root = "apps/api"
   install = false
   pyproject = "apps/api/pyproject.toml"
   lockfile = "apps/api/uv.lock"
   ```

   `install = false` is enough for `from app.agent.prompts import format_context`. Set `install = true` when a probe must import modules that need the lockfile. The install uses the base lockfile, needs network only in the install container, and can take several minutes for HelpDeck's full set.

2. Point the workflow at a ProofMerge revision that contains this layout support. Until that revision is on `main`, use the branch ref instead of `@main`:

   ```yaml
   - uses: imthi16/proofmerge@cursor/monorepo-python-support-8043
     with:
       model-api-key: ${{ secrets.MODEL_API_KEY }}
       fail-on: block
       python-version: '3.13'
       # fail-on-missing-key: true   # optional; default false stays green when the secret is empty
   ```

   For a same-repository pull request, the workflow file from the pull request head is the one that runs if that pull request edits it. Updating `uses:` on the regression pull request is an owner edit to that workflow; this repository does not change that pull request. Putting the workflow on main does not by itself override a workflow file the pull request already modifies.

3. An API-backed `proofmerge verify` still needs, on the HelpDeck repository:

   - Actions secret `MODEL_API_KEY` (a Meta key). An empty secret skips the action when `fail-on-missing-key` is false.
   - The default endpoint `https://api.meta.ai/v1`, Spark model `muse-spark-1.3`, Wonder model `muse-wonder-1.0`. Override with `PROOFMERGE_BASE_URL`, `PROOFMERGE_SPARK_MODEL`, and `PROOFMERGE_WONDER_MODEL` only if you intend to.
   - Docker on the runner (`ubuntu-latest` is enough) and `fetch-depth: 0` so both SHAs are present.
   - The action ref above, or `imthi16/proofmerge@main` after this change is merged.

   The run that would justify BLOCK is one ProofMerge generates itself: a probe that passes 3/3 on the base commit and fails 3/3 on the head commit, classified as a verified regression against an uncontested PRESERVE claim. A hand-written probe that shows the two commits print different text is a compatibility check of the runner, not that verdict.

For a local check before the config is on main, pass a file that is not the pull request checkout:

```bash
proofmerge verify /path/to/helpdeck \
  --base e7b5e7af93a4c410cc6595e13b2a30a8dd2fe81c \
  --head 479d67227dcccd824624cf6c4984c1e816d9542b \
  --config /path/to/operator.proofmerge.toml \
  --intent-file /path/to/intent.txt \
  --evidence-dir /tmp/helpdeck-evidence
```
