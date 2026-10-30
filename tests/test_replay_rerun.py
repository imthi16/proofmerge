"""Replay uses sealed probes, immutable revisions and the existing isolated runner."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_pipeline import INTENT, Replies, execute
from test_probes import fake_runner, git
from test_sandbox import ROOT, needs_docker
from typer.testing import CliRunner

from proofmerge import cli, sandbox
from proofmerge.evidence import Proof
from proofmerge.manifest import verify_manifest, write_manifest
from proofmerge.meta import MetaClient
from proofmerge.pipeline import run_verify

pytest_plugins = ["test_sandbox"]


def snapshot(directory):
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def rewrite(proof_path, change):
    data = json.loads(proof_path.read_text())
    change(data)
    proof_path.write_text(json.dumps(data))
    write_manifest(proof_path.parent)


def invoke(proof_path, repo=None, *, rerun=True):
    args = ["replay", str(proof_path)]
    if rerun:
        args.append("--rerun")
    if repo is not None:
        args.extend(["--repo", str(repo)])
    return CliRunner().invoke(cli.app, args)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    proof, evidence, _, _ = execute(tmp_path)

    def no_models(*args, **kwargs):
        pytest.fail("replay must never call a model")

    monkeypatch.setattr(cli, "MetaClient", no_models)
    monkeypatch.setattr(MetaClient, "structured", no_models)
    return evidence / "proof.json", tmp_path / "repo", proof


@pytest.fixture
def runs(monkeypatch, tmp_path):
    calls = []
    runner_calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return sandbox.run_experiment(
            *args, **kwargs, runner=fake_runner(runner_calls), digest="sha256:fake",
        )

    def make_temp(*, prefix):
        destination = tmp_path / f"{prefix}{len(calls)}"
        destination.mkdir()
        return str(destination)

    monkeypatch.setattr(cli, "run_experiment", run)
    monkeypatch.setattr(cli, "tempfile", SimpleNamespace(mkdtemp=make_temp))
    return calls, runner_calls


def test_reproduced_uses_full_shas_source_attempts_and_new_evidence(bundle, runs):
    path, repo, proof = bundle
    before = snapshot(path.parent)
    # Refs can move; only the proof's pinned commits may reach the runner.
    git(repo, "checkout", "-q", "--detach", proof.base_sha)
    result = invoke(path, repo)
    assert result.exit_code == 0, result.output
    assert "H1 base PPP head FFF: reproduced\n" in result.stdout
    calls, runner_calls = runs
    assert runner_calls == ["base"] * 3 + ["head"] * 3
    (args, kwargs), = calls
    assert args[:4] == (repo, proof.base_sha, proof.head_sha, "H1")
    assert args[4] == (path.parent / proof.experiments[0].test_source_path).read_text()
    destination = args[5]
    assert destination != path.parent and destination.is_dir()
    assert f"re-run evidence: {destination}\n" in result.stdout
    assert (destination / "H1/head-3.stdout").is_file()
    assert kwargs == {"attempts": 3, "timeout": proof.experiments[0].timeout_seconds}
    assert snapshot(path.parent) == before
    assert verify_manifest(path.parent) == []


def test_one_attempt_differs_names_experiment_and_runs_remaining(bundle, runs, monkeypatch):
    path, repo, _ = bundle

    def add_experiment(data):
        other = dict(data["experiments"][0], hypothesis_id="T1")
        data["experiments"].append(other)

    rewrite(path, add_experiment)
    run = cli.run_experiment

    def differs(*args, **kwargs):
        experiment = run(*args, **kwargs)
        if args[3] == "H1":
            experiment.executions[4].outcome = "PASS"
        return experiment

    monkeypatch.setattr(cli, "run_experiment", differs)
    result = invoke(path, repo)
    assert result.exit_code == 1, result.output
    assert "H1 recorded base PPP head FFF, now base PPP head FPF: NOT reproduced" in result.stdout
    assert "T1 base PPP head FFF: reproduced" in result.stdout
    assert len(runs[0]) == 2


def test_recorded_count_timeout_and_attempt_order_are_preserved(bundle, runs):
    path, repo, _ = bundle

    def change(data):
        experiment = data["experiments"][0]
        experiment["timeout_seconds"] = 17
        experiment["executions"] = list(reversed([
            e for e in experiment["executions"] if e["attempt"] <= 2
        ]))

    rewrite(path, change)
    result = invoke(path, repo)
    assert result.exit_code == 0, result.output
    assert "H1 base PP head FF: reproduced" in result.stdout
    assert runs[0][0][1] == {"attempts": 2, "timeout": 17}
    assert runs[1] == ["base"] * 2 + ["head"] * 2


def test_probe_line_endings_are_preserved(bundle, runs):
    path, repo, proof = bundle
    source_path = path.parent / proof.experiments[0].test_source_path
    source = source_path.read_bytes().replace(b"\n", b"\r\n")
    source_path.write_bytes(source)

    def change(data):
        data["experiments"][0]["test_source_sha256"] = hashlib.sha256(source).hexdigest()

    rewrite(path, change)
    result = invoke(path, repo)
    assert result.exit_code == 0, result.output
    assert runs[0][0][0][4].encode("utf-8") == source


@pytest.mark.parametrize("outcome,letter", [("FAIL", "F"), ("ERROR", "E"), ("TIMEOUT", "T")])
def test_base_outcome_changes_also_fail(bundle, runs, monkeypatch, outcome, letter):
    path, repo, _ = bundle
    run = cli.run_experiment

    def differs(*args, **kwargs):
        experiment = run(*args, **kwargs)
        experiment.executions[1].outcome = outcome
        return experiment

    monkeypatch.setattr(cli, "run_experiment", differs)
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert (f"H1 recorded base PPP head FFF, now base P{letter}P head FFF: NOT reproduced"
            in result.stdout)


@pytest.mark.parametrize("rewrite_manifest", [False, True])
def test_tampered_probe_stops_before_git_or_runner(bundle, runs, monkeypatch, rewrite_manifest):
    path, repo, proof = bundle
    (path.parent / proof.experiments[0].test_source_path).write_text("tampered")
    if rewrite_manifest:
        write_manifest(path.parent)

    def no_git(*args, **kwargs):
        pytest.fail("hash failures must stop before git")

    monkeypatch.setattr(cli.subprocess, "run", no_git)
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert "Probe hash mismatch" in result.stdout
    assert runs[0] == []
    assert "re-run evidence:" not in result.stdout


@pytest.mark.parametrize("field", ["base_sha", "head_sha"])
@pytest.mark.parametrize("sha", ["main", "abc123", "--output=/tmp/unsafe", "f" * 40 + "\n"])
def test_only_full_shas_reach_git(bundle, runs, monkeypatch, field, sha):
    path, repo, _ = bundle
    rewrite(path, lambda data: data.update({field: sha}))

    def no_git(*args, **kwargs):
        pytest.fail("invalid SHAs must not reach git")

    monkeypatch.setattr(cli.subprocess, "run", no_git)
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert "not a full commit SHA" in result.output
    assert runs[0] == []


@pytest.mark.parametrize("field", ["base_sha", "head_sha"])
def test_missing_sha_stops_before_any_run(bundle, runs, field):
    path, repo, _ = bundle
    rewrite(path, lambda data: data.update({field: "0" * 40}))
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert f"lacks recorded commit {'0' * 40}" in result.output
    assert runs[0] == []
    assert "re-run evidence:" not in result.stdout


def test_non_commit_object_is_rejected(bundle, runs):
    path, repo, _ = bundle
    blob = git(repo, "rev-parse", "HEAD:calc.py")
    rewrite(path, lambda data: data.update(head_sha=blob))
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert f"lacks recorded commit {blob}" in result.output
    assert runs[0] == []


def test_repo_required_only_for_rerun(bundle, runs):
    path, _, _ = bundle
    result = invoke(path)
    assert result.exit_code == 1
    assert "--repo is required with --rerun" in result.output
    result = invoke(path, rerun=False)
    assert result.exit_code == 0
    assert result.stdout.startswith("evidence intact:")
    assert result.stdout.count("\n") == 1
    assert runs[0] == []


@pytest.mark.parametrize("bad", ["no-experiments", "no-base", "unequal", "attempt", "commit",
                                 "timeout", "id", "duplicate"])
def test_malformed_records_fail_before_any_run(bundle, runs, bad):
    path, repo, _ = bundle

    def change(data):
        experiment = data["experiments"][0]
        if bad == "no-experiments":
            data["experiments"] = []
        elif bad == "no-base":
            experiment["executions"] = [
                e for e in experiment["executions"] if e["revision"] == "head"
            ]
        elif bad == "unequal":
            experiment["executions"].pop()
        elif bad == "attempt":
            experiment["executions"][0]["attempt"] = 2
        elif bad == "commit":
            experiment["executions"][0]["commit_sha"] = "main"
        elif bad == "timeout":
            experiment["timeout_seconds"] = 0
        elif bad == "id":
            experiment["hypothesis_id"] = "../H1"
        else:
            data["experiments"].append(experiment)

    rewrite(path, change)
    result = invoke(path, repo)
    assert result.exit_code == 1, result.output
    assert "replay failed:" in result.output
    assert runs[0] == []


def test_docker_exit_125_matching_error_outcomes_are_not_reproduced(bundle, monkeypatch, tmp_path):
    path, repo, _ = bundle

    def all_error(data):
        for execution in data["experiments"][0]["executions"]:
            execution["outcome"] = "ERROR"
            execution["exit_code"] = 2

    rewrite(path, all_error)
    proof = Proof.model_validate_json(path.read_text())
    experiment = proof.experiments[0]

    def docker_fail(*args, **kwargs):
        executions = [
            execution.model_copy(update={"outcome": "ERROR", "exit_code": 125})
            for execution in experiment.executions
        ]
        return experiment.model_copy(update={"executions": executions})

    def make_temp(*, prefix):
        destination = tmp_path / "rerun-evidence"
        destination.mkdir(exist_ok=True)
        return str(destination)

    monkeypatch.setattr(cli, "run_experiment", docker_fail)
    monkeypatch.setattr(cli, "tempfile", SimpleNamespace(mkdtemp=make_temp))
    result = invoke(path, repo)
    assert result.exit_code == 1, result.output
    assert "H1 recorded base EEE head EEE: NOT reproduced (runner failure)" in result.stdout


@pytest.mark.parametrize("error", [sandbox.SandboxError("image unavailable"),
                                 FileNotFoundError("docker missing")])
def test_runner_failure_is_clear_and_keeps_new_evidence(bundle, runs, monkeypatch, error):
    path, repo, _ = bundle
    before = snapshot(path.parent)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "run_experiment", fail)
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert f"H1 recorded base PPP head FFF: NOT reproduced ({error})" in result.stdout
    destination = Path(result.stdout.split("re-run evidence: ")[1].splitlines()[0])
    assert destination.is_dir()
    assert snapshot(path.parent) == before


@needs_docker
def test_docker_demo_proof_reproduces(demo_repo, tmp_path, monkeypatch):
    probe = (ROOT / "demo/ground_truth/tenant-cache-regression/test_cross_tenant.py").read_text()
    evidence = tmp_path / "docker-evidence"
    proof = run_verify(
        demo_repo, "main", "candidate/tenant-cache-regression", INTENT,
        evidence, MetaClient(Replies(location="src/demo_shop/pricing.py::price_for", probe=probe)),
    )
    assert proof.verdict.decision == "BLOCK"
    before = snapshot(evidence)

    def no_models(*args, **kwargs):
        pytest.fail("replay must never call a model")

    monkeypatch.setattr(MetaClient, "structured", no_models)
    result = invoke(evidence / "proof.json", demo_repo)
    assert result.exit_code == 0, result.output
    assert "H1 base PPP head FFF: reproduced" in result.stdout
    destination = Path(result.stdout.split("re-run evidence: ")[1].splitlines()[0])
    assert (destination / "H1/head-3.stdout").is_file()
    assert snapshot(evidence) == before


def test_config_language_reruns_with_base_config(bundle, monkeypatch):
    path, repo, proof = bundle
    git(repo, "checkout", "-q", proof.base_sha)
    (repo / ".proofmerge.toml").write_text(
        'language = "go"\nimage = "golang:1.23-alpine"\nprobe_path = "p_test.go"\n'
        'test_command = ["go", "test"]\nassertion_failure = "--- FAIL"\n'
    )
    git(repo, "add", ".proofmerge.toml")
    git(repo, "commit", "-q", "-m", "config")
    base = git(repo, "rev-parse", "HEAD").strip()
    seen = []

    def run_config(repo_arg, base_sha, head_sha, identifier, source, config, *args, **kwargs):
        seen.append((base_sha, config.language))
        experiment = next(e for e in Proof.model_validate_json(path.read_text()).experiments
                          if e.hypothesis_id == identifier)
        return experiment

    monkeypatch.setattr(cli, "run_config_experiment", run_config)
    monkeypatch.setattr(cli, "run_experiment", lambda *a, **k: pytest.fail("pytest runner used"))

    def to_config_base(data):
        data.update(language="go", base_sha=base)
        for experiment in data["experiments"]:
            for execution in experiment["executions"]:
                if execution["revision"] == "base":
                    execution["commit_sha"] = base

    rewrite(path, to_config_base)
    result = invoke(path, repo)
    assert result.exit_code == 0, result.output
    assert seen and all(entry == (base, "go") for entry in seen)


def test_config_language_without_base_config_fails(bundle, monkeypatch):
    path, repo, _ = bundle
    monkeypatch.setattr(cli, "run_config_experiment", lambda *a, **k: pytest.fail("ran"))
    rewrite(path, lambda data: data.update(language="go"))
    result = invoke(path, repo)
    assert result.exit_code == 1
    assert "no .proofmerge.toml for go" in result.output
