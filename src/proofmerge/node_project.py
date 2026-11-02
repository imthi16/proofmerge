"""Node.js and TypeScript repositories that have no `.proofmerge.toml`.

When the base commit has `package.json` and `package-lock.json` at its root and no
Python project files, ProofMerge sets up the generic runner itself:

- The image is built from the base commit's `package.json` and `package-lock.json`
  with `npm ci --ignore-scripts`, plus `tsx`. No repository script runs, and the
  network is used only while the image builds, never in a probe container. The tag is
  a hash of the Dockerfile and both files, so each lockfile is built once.
- A probe is one Node test-runner file (`node:test`, `node:assert`) run with
  `node --import <the image's tsx loader> --test`. The loader is named by an absolute
  path outside the repository, so a `node_modules/tsx` committed in the change cannot
  replace it. The repository's own Vite, Vitest or Jest config is never loaded.
- Every `tsconfig*.json` and `jsconfig*.json` in the tested tree is replaced by the
  base commit's copy, so a change cannot redirect imports (`paths`) for its own test.

Like the base commit's `.proofmerge.toml`, everything here is read from the base
commit, so a pull request cannot change how it is tested.
"""

import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proofmerge.project_config import ProjectConfig

PYTHON_MARKERS = {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"}
BASE_IMAGE = "node:22-slim"
TSX = "tsx@4.19.2"
# Absolute: a bare "tsx" would resolve /work/node_modules/tsx first, which the change controls.
TSX_LOADER = "file:///node_modules/tsx/dist/loader.mjs"
CONFIG_FILE = re.compile(r"(?:^|/)[tj]sconfig[^/]*\.json$")
DOCKERFILE = f"""FROM {BASE_IMAGE}
WORKDIR /
COPY package.json package-lock.json /
RUN npm ci --ignore-scripts --no-audit --no-fund \\
 && npm install --no-save --ignore-scripts --no-audit --no-fund {TSX} \\
 && rm -rf /root/.npm
"""
BUILD_TIMEOUT = 900
PROBE_NOTE = """
This repository is JavaScript or TypeScript, set up by ProofMerge:
- Write the probe with Node's own test runner: `import { test } from "node:test";` and `import assert from "node:assert/strict";`. Do not import vitest, jest, mocha or any other test framework; they are not running.
- The probe file sits at the repository root. Import the code under test by a relative path from there, without a file extension (for example `./src/pricing`).
- Use `assert.equal`, `assert.deepEqual` or `assert.throws`, so a failed check prints AssertionError.
"""
Runner = Callable[..., Any]


def _git(repo: Path, runner: Runner, *args: str) -> Any:
    return runner(["git", "-C", str(repo), *args], capture_output=True, check=False)


def detect(repo: Path, base_sha: str, runner: Runner = subprocess.run) -> ProjectConfig | None:
    """A runner config for a Node project at `base_sha`, or None when it is not one."""
    listed = _git(repo, runner, "ls-tree", "--name-only", base_sha)
    names = set(listed.stdout.decode().splitlines()) if listed.returncode == 0 else set()
    if "package.json" not in names or names & PYTHON_MARKERS:
        return None
    if "package-lock.json" not in names:
        raise ValueError(
            "this is a Node project, but its base commit has no package-lock.json; "
            "ProofMerge installs dependencies with npm ci. For yarn or pnpm, add a "
            ".proofmerge.toml (see docs/languages.md)"
        )
    package = _git(repo, runner, "show", f"{base_sha}:package.json").stdout
    lock = _git(repo, runner, "show", f"{base_sha}:package-lock.json").stdout
    try:
        manifest = json.loads(package)
        declared = {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}
    except (ValueError, AttributeError) as error:
        raise ValueError(f"package.json at {base_sha} is not a JSON object") from error
    typescript = "tsconfig.json" in names or "typescript" in declared
    digest = hashlib.sha256(DOCKERFILE.encode() + b"\0" + package + b"\0" + lock).hexdigest()
    tag = f"proofmerge-node:{digest[:16]}"
    ensure_image(tag, package, lock, runner)
    probe = "proofmerge_probe.test.ts" if typescript else "proofmerge_probe.test.mjs"
    return ProjectConfig(
        language="typescript" if typescript else "javascript",
        image=tag,
        probe_path=probe,
        test_command=["node", "--import", TSX_LOADER, "--test", probe],
        assertion_failure=r"\bAssertionError\b",
        env={"HOME": "/tmp", "NO_COLOR": "1"},
    )


def ensure_image(tag: str, package: bytes, lock: bytes, runner: Runner = subprocess.run) -> None:
    if runner(["docker", "image", "inspect", tag], capture_output=True, check=False).returncode == 0:
        return
    with tempfile.TemporaryDirectory(prefix="proofmerge-node-") as folder:
        context = Path(folder)
        (context / "Dockerfile").write_text(DOCKERFILE)
        (context / "package.json").write_bytes(package)
        (context / "package-lock.json").write_bytes(lock)
        try:
            built = runner(["docker", "build", "--quiet", "--tag", tag, str(context)],
                           capture_output=True, check=False, timeout=BUILD_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ValueError(f"building the Node image took over {BUILD_TIMEOUT} seconds") from None
    if built.returncode != 0:
        tail = (built.stderr or b"")[-800:].decode(errors="replace").strip()
        raise ValueError(f"could not build the Node image for this repository (npm ci failed):\n{tail}")


def is_auto(config: ProjectConfig | None) -> bool:
    return config is not None and config.image.startswith("proofmerge-node:") and \
        config.test_command[:3] == ["node", "--import", TSX_LOADER]


def base_configs(repo: Path, base_sha: str, runner: Runner = subprocess.run) -> dict[str, bytes]:
    """Every tsconfig*.json and jsconfig*.json in the base commit, by path."""
    listed = _git(repo, runner, "ls-tree", "-r", "-z", "--name-only", base_sha)
    if listed.returncode != 0:
        raise ValueError(f"could not list the files of {base_sha}")
    names = [n for n in listed.stdout.decode(errors="replace").split("\0") if CONFIG_FILE.search(n)]
    return {n: _git(repo, runner, "show", f"{base_sha}:{n}").stdout for n in names}


def pin_configs(code_dir: Path, configs: dict[str, bytes]) -> None:
    """Make an exported tree use the base commit's TypeScript configs and no others."""
    # shortcut: a config `extends` target with another name is not pinned; pin it too if one shows up.
    for path in list(code_dir.rglob("*")):
        rel = path.relative_to(code_dir).as_posix()
        if CONFIG_FILE.search(rel) and (path.is_symlink() or path.is_file()):
            path.unlink()
    for rel, data in configs.items():
        target = code_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
