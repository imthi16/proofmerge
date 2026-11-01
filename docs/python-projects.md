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
lockfile = "apps/api/uv.lock"       # uv.lock, poetry.lock or Pipfile.lock; default <root>/uv.lock
pyproject = "apps/api/pyproject.toml"  # optional cross-check against the lockfile
# requirements = "requirements.txt"  # alternative to a lockfile; every line must be name==version with sha256 hashes
python_version = "3.13"    # runner image, used only to evaluate lockfile markers
```

`root` omitted and `pythonpath` empty keeps `PYTHONPATH=/work/code/src:/work/code`.

## Dependency install

`install = false` is the default. Nothing is downloaded, and probe containers stay on `--network none`.

`install = true` reads the lockfile or requirements file from the **base** commit, not from the head tree and not by executing project code. A `uv.lock` is reduced to `name==version` lines with the sha256 of every published wheel. A `poetry.lock` is read the same way, starting from the main dependencies in the base commit's `pyproject.toml` (`[project].dependencies` or `[tool.poetry.dependencies]`; `pyproject` defaults to the file next to the lockfile): dev groups and optional dependencies without a requested extra are not installed, and only wheel hashes are kept. A `Pipfile.lock` is already resolved: its `default` packages are installed and `develop` is not. Its hashes do not say which file is a wheel, so an sdist-only package fails the install (`--only-binary=:all:`) instead of running `setup.py`. Every format must use one https package index. The project itself is not installed; the exported tree stays on `PYTHONPATH`. Path, git, and URL packages are refused. Packages with no wheel are refused, so an sdist cannot run `setup.py`. Marker expressions are evaluated for CPython on Linux at `python_version`, using this machine's CPU architecture because a native container shares it. An unknown marker variable fails the install.

The install runs in its own container: network is `bridge` (so the index in the lockfile can be reached), capabilities are dropped, the host environment is not passed (`MODEL_API_KEY` is not in the container), the root filesystem is read-only, and the dependency directory is the only writable mount. `pip install` is invoked with `--require-hashes`, `--only-binary=:all:`, and `--no-deps`. Probe containers then mount that directory read-only at `/deps` and still use `--network none`. The install container runs as the host's own non-root UID:GID, so a non-root CI runner owns the installed files and can make them readable for the probe user and delete them afterwards; on a root host it runs as `65534:65534`, never as root.

If head's lockfile differs from base, the proof records that head's dependency file was not installed. A new import that needs a package only the pull request added fails as an environment error.

## What an import error means

A pytest assertion failure (exit 1) is a behavioral FAIL. `ModuleNotFoundError` or `ImportError` on any other exit, a missing import path, or a failed install is recorded as `setup_failure` and classified `ENVIRONMENT`. The verdict is REVIEW. It is not PASS and it is not BLOCK, including when every attempt on both revisions fails the same way.

## Layouts

Pick the table that matches where your importable package lives. All paths are
repository-relative and read from the base commit.

| Repository layout | `.proofmerge.toml` |
|---|---|
| Package at the root (`mypkg/`) or under `src/` (`src/mypkg/`) | none needed: the default `PYTHONPATH` covers both |
| Monorepo, one service (`services/api/app/`) | `[python]` with `root = "services/api"` |
| Monorepo with shared code (`apps/web/` does `import shared`, which lives at `libs/shared/`) | `root = "apps/web"`, `pythonpath = ["libs"]` |
| Third-party imports locked with uv | add `install = true`; `lockfile` defaults to `<root>/uv.lock` |
| Third-party imports locked with Poetry | `install = true`, `lockfile = "<root>/poetry.lock"` (reads the `pyproject.toml` beside it) |
| Third-party imports locked with Pipenv | `install = true`, `lockfile = "<root>/Pipfile.lock"` |
| Third-party imports locked with pip-tools or `pip freeze --require-hashes` | `install = true`, `requirements = "requirements.txt"` (every line `name==version --hash=sha256:...`) |

```toml
# Example: a Poetry project, with its pyproject.toml next to the lockfile.
[python]
root = "backend"
install = true
lockfile = "backend/poetry.lock"
```

```toml
# Example: a service in a monorepo that imports a shared library and needs its locked packages.
[python]
root = "services/api"
pythonpath = ["libs"]
install = true
lockfile = "services/api/uv.lock"
pyproject = "services/api/pyproject.toml"
```

Leave `install = false` when the code a probe imports uses only the standard library
and repository code: nothing is downloaded and the run is faster.

## Adopting on a repository

1. Land `.proofmerge.toml` on the default branch in its own pull request, before the
   pull requests you want verified. ProofMerge reads the file from the base SHA, so a
   pull request picks it up only once its base contains that commit. Re-run the
   workflow on an open pull request after the default branch moves.
2. Use the action as in [github-action.md](github-action.md) (`imthi16/proofmerge@main`
   once this layout support is merged).
3. Check the layout locally first. `--config` lets you try a file that is not yet on
   the base branch; keep it outside the checkout:

   ```bash
   proofmerge verify /path/to/repo --base main --head my-branch \
     --config /path/to/operator.proofmerge.toml \
     --intent-file /path/to/intent.txt \
     --evidence-dir /tmp/proofmerge-evidence
   ```

A BLOCK still needs a probe that ProofMerge generates itself, passing 3/3 on the base
commit and failing 3/3 on the head commit, against an uncontested PRESERVE claim. A
hand-written probe that shows two commits behave differently checks the runner; it is
not that verdict.

## Not supported yet

- Unhashed `requirements.txt` and lockfiles of other tools (PDM, Conda) cannot be
  installed. Export hashed requirements instead, or keep `install = false`.
- In `poetry.lock`, a dependency's `python = "..."` constraint is not evaluated; its
  `markers` are. A package that turns out not to install fails as REVIEW.
- Packages without a wheel (sdist only), and path, git or URL dependencies, are refused.
- Only one `[python]` table: one project root per repository.
