"""Node and TypeScript repositories without a .proofmerge.toml: detection, image, probe prompt."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge import node_project
from proofmerge.node_project import (
    DOCKERFILE,
    PROBE_NOTE,
    TSX_LOADER,
    base_configs,
    detect,
    ensure_image,
    is_auto,
    pin_configs,
)
from proofmerge.probes import _probe_prompt
from proofmerge.project_config import ProjectConfig

DEMO = Path(__file__).parent.parent / "demo"
PACKAGE = json.dumps({"devDependencies": {"vitest": "3.2.4"}}).encode()


class FakeGit:
    """`git ls-tree` and `git show` for one base commit; docker image inspect and build."""

    def __init__(self, files: dict[str, bytes], image_exists: bool = False, build_ok: bool = True) -> None:
        self.files, self.image_exists, self.build_ok = files, image_exists, build_ok
        self.built: list[str] = []

    def __call__(self, argv, **_):
        if argv[:2] == ["docker", "image"]:
            return SimpleNamespace(returncode=0 if self.image_exists else 1)
        if argv[:2] == ["docker", "build"]:
            context = Path(argv[-1])
            self.built.append((context / "Dockerfile").read_text())
            assert (context / "package-lock.json").read_bytes() == self.files["package-lock.json"]
            return SimpleNamespace(returncode=0 if self.build_ok else 1, stderr=b"npm ERR! 404 left-pad")
        if argv[3] == "ls-tree":
            return SimpleNamespace(returncode=0, stdout="\n".join(self.files).encode())
        name = argv[-1].split(":", 1)[1]
        return SimpleNamespace(returncode=0, stdout=self.files[name])


def test_a_repo_without_package_json_or_with_python_files_keeps_the_python_runner(tmp_path: Path) -> None:
    assert detect(tmp_path, "abc", FakeGit({"README.md": b""})) is None
    both = {"package.json": PACKAGE, "package-lock.json": b"{}", "pyproject.toml": b""}
    assert detect(tmp_path, "abc", FakeGit(both)) is None


def test_npm_lockfile_is_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no package-lock.json"):
        detect(tmp_path, "abc", FakeGit({"package.json": PACKAGE, "yarn.lock": b""}))


def test_typescript_project_gets_a_node_test_runner_config(tmp_path: Path) -> None:
    git = FakeGit({"package.json": PACKAGE, "package-lock.json": b'{"v": 1}', "tsconfig.json": b"{}"})
    config = detect(tmp_path, "abc", git)
    assert config.language == "typescript" and config.probe_path == "proofmerge_probe.test.ts"
    assert config.test_command == ["node", "--import", TSX_LOADER, "--test", "proofmerge_probe.test.ts"]
    assert config.image.startswith("proofmerge-node:") and is_auto(config)
    # Repository scripts never run while the image builds.
    assert git.built == [DOCKERFILE] and "npm ci --ignore-scripts" in DOCKERFILE

    plain = detect(tmp_path, "abc", FakeGit({"package.json": b"{}", "package-lock.json": b'{"v": 1}'}))
    assert plain.language == "javascript" and plain.probe_path.endswith(".mjs")


def test_the_image_tag_follows_the_lockfile_and_is_built_once(tmp_path: Path) -> None:
    files = {"package.json": PACKAGE, "package-lock.json": b'{"v": 1}'}
    one = detect(tmp_path, "abc", FakeGit(files)).image
    assert detect(tmp_path, "abc", FakeGit(files)).image == one
    assert detect(tmp_path, "abc", FakeGit(files | {"package-lock.json": b'{"v": 2}'})).image != one
    cached = FakeGit(files, image_exists=True)
    detect(tmp_path, "abc", cached)
    assert cached.built == []


def test_a_failed_npm_ci_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="npm ci failed"):
        ensure_image("proofmerge-node:x", PACKAGE, b"{}", FakeGit({"package-lock.json": b"{}"}, build_ok=False))


def test_only_the_auto_config_tells_spark_to_use_node_test() -> None:
    auto = ProjectConfig(language="typescript", image="proofmerge-node:abc", probe_path="p.test.ts",
                         test_command=["node", "--import", TSX_LOADER, "--test", "p.test.ts"],
                         assertion_failure="AssertionError")
    go = ProjectConfig(language="go", image="golang:1.23-alpine", probe_path="x_test.go",
                       test_command=["go", "test"], assertion_failure="FAIL")
    assert _probe_prompt(auto).endswith(PROBE_NOTE) and "node:test" in _probe_prompt(auto)
    assert "node:test" not in _probe_prompt(go)


def test_both_revisions_run_with_the_base_typescript_configs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "packages" / "a").mkdir(parents=True)
    (repo / "tsconfig.json").write_text('{"compilerOptions": {"paths": {}}}')
    (repo / "packages" / "a" / "tsconfig.build.json").write_text("{}")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.decode().strip()
    configs = base_configs(repo, base)
    assert set(configs) == {"tsconfig.json", "packages/a/tsconfig.build.json"}

    head = tmp_path / "head"  # what the change exported: an edited config, a new one, a symlinked one
    (head / "packages" / "a").mkdir(parents=True)
    (head / "tsconfig.json").write_text('{"compilerOptions": {"paths": {"./src/*": ["./evil/*"]}}}')
    (head / "jsconfig.json").write_text("{}")
    (head / "packages" / "a" / "tsconfig.build.json").symlink_to(head / "tsconfig.json")
    (head / "src.ts").write_text("export {};")
    pin_configs(head, configs)
    assert (head / "tsconfig.json").read_bytes() == configs["tsconfig.json"]
    assert not (head / "jsconfig.json").exists() and (head / "src.ts").exists()
    built = head / "packages" / "a" / "tsconfig.build.json"
    assert not built.is_symlink() and built.read_text() == "{}"


def _docker_works() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], check=False, capture_output=True).returncode == 0


if os.environ.get("PROOFMERGE_REQUIRE_DOCKER") == "1" and not _docker_works():
    raise RuntimeError("PROOFMERGE_REQUIRE_DOCKER=1 but docker is not available")


@pytest.mark.skipif(not _docker_works(), reason="docker not available")
def test_demo_probe_passes_on_base_and_fails_on_head_in_docker(tmp_path: Path) -> None:
    from proofmerge.project_config import load_setup
    from proofmerge.sandbox import run_config_experiment

    sys.path.insert(0, str(DEMO))
    import build_ts_demo

    repo = tmp_path / "ts-shop"
    build_ts_demo.build(repo, force=False)
    rev = lambda ref: subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                                     capture_output=True, text=True, check=True).stdout.strip()
    base, head = rev("main"), rev("candidate/expense-totals-refactor")
    config, python = load_setup(repo, base)  # builds the image from the lockfile (network)
    assert python is None and is_auto(config) and node_project.BASE_IMAGE in DOCKERFILE
    probe = (
        'import { test } from "node:test";\n'
        'import assert from "node:assert/strict";\n'
        'import { reimbursableCents } from "./src/expenses/totals";\n'
        'test("drafts are never paid", () => {\n'
        '  assert.equal(reimbursableCents([\n'
        '    { id: "a", category: "office", amountCents: 1000, status: "approved" },\n'
        '    { id: "d", category: "office", amountCents: 500, status: "draft" },\n'
        "  ]), 1000);\n"
        "});\n"
    )
    found = run_config_experiment(repo, base, head, "H1", probe, config, tmp_path / "ev", attempts=1, timeout=120)
    assert [(e.revision, e.outcome) for e in found.executions] == [("base", "PASS"), ("head", "FAIL")]
    # A change that ships its own node_modules/tsx cannot swap the loader and pass.
    hijack = repo / "node_modules" / "tsx"
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "candidate/expense-totals-refactor"], check=True)
    (hijack / "dist").mkdir(parents=True)
    (hijack / "package.json").write_text('{"name": "tsx", "type": "module", "exports": {".": "./dist/loader.mjs"}}')
    (hijack / "dist" / "loader.mjs").write_text("process.exit(0);\n")
    subprocess.run(["git", "-C", str(repo), "add", "-f", "node_modules"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "tsx"],
                   check=True)
    found = run_config_experiment(repo, base, rev("HEAD"), "H3", probe, config, tmp_path / "ev",
                                  attempts=1, timeout=120)
    assert [(e.revision, e.outcome) for e in found.executions] == [("base", "PASS"), ("head", "FAIL")]
    framework = probe.replace('import { test } from "node:test";', 'import { test } from "vitest";')
    found = run_config_experiment(repo, base, head, "H2", framework, config, tmp_path / "ev", attempts=1, timeout=120)
    assert {e.outcome for e in found.executions} == {"ERROR"}  # a probe that cannot run is never evidence
