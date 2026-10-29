"""Offline end-to-end verification plus one optional owned-fixture Docker run."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_hypotheses import CONTRACT, hypothesis
from test_meta import FakeCompletions
from test_probes import ScriptedCompletions, fake_runner, git, make_repo
from test_sandbox import ROOT, needs_docker
from typer.testing import CliRunner

from proofmerge import __version__, cli, pipeline
from proofmerge.context import ContextError
from proofmerge.evidence import ModelUsage, Proof, TamperFinding
from proofmerge.hypotheses import MAX_DIFF_CHARS, MAX_HYPOTHESES
from proofmerge.manifest import verify_manifest, write_manifest
from proofmerge.meta import MetaClient, MetaError
from proofmerge.pipeline import run_verify
from proofmerge.probes import MAX_BASE_RUNS
from proofmerge.sandbox import SandboxError
from proofmerge.tamper import TamperError

pytest_plugins = ["test_sandbox"]

INTENT = "Returned prices must not change."
PROBE = "# GOOD\nfrom calc import double\ndef test_double():\n    assert double(3) == 6\n"


class Replies:
    """Script the two structured stages, then reuse the probe completion fake."""

    def __init__(self, *, claim="C1", location="calc.py::double", fail=None, probe=PROBE):
        self.replies = [
            {"claims": [CONTRACT.claims[1].model_dump(exclude={"id"})]},
            {"hypotheses": [hypothesis(claim, [location])]},
        ]
        if claim == "C0":
            self.replies.pop(0)
        self.probes = ScriptedCompletions(probe)
        self.fail = fail
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == self.fail:
            raise MetaError("scripted API unavailable")
        if not self.replies:
            return self.probes.create(**kwargs)
        response = FakeCompletions(content=json.dumps(self.replies.pop(0))).create(**kwargs)
        response.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
        return response


def execute(tmp_path, *, replies=None, intent=INTENT, runner=None):
    repo, context = make_repo(tmp_path)
    client = MetaClient(replies or Replies())
    evidence = tmp_path / "evidence"
    proof = run_verify(
        repo, context.base_sha, context.head_sha, intent, evidence, client,
        runner=runner or fake_runner([]), digest="sha256:fake",
    )
    return proof, evidence, client, context


def assert_bundle(proof, evidence):
    assert Proof.model_validate_json((evidence / "proof.json").read_text()) == proof
    assert proof.verdict.decision in (evidence / "report.html").read_text()
    assert verify_manifest(evidence) == []
    result = CliRunner().invoke(cli.app, ["replay", str(evidence / "proof.json")])
    assert result.exit_code == 0, result.output


def test_regression_bundle_and_last_write(tmp_path, monkeypatch):
    def seal(evidence):
        assert (evidence / "proof.json").is_file()
        assert (evidence / "report.html").is_file()
        write_manifest(evidence)
        # Any subsequent write would invalidate this snapshot (including manifest).
        snapshots.update({p: p.read_bytes() for p in evidence.rglob("*") if p.is_file()})

    snapshots = {}
    monkeypatch.setattr(pipeline, "write_manifest", seal)
    # A sentinel verifies that environment credentials are never included in the bundle.
    monkeypatch.setenv("MODEL_API_KEY", "test-secret-must-stay-out")
    proof, evidence, client, context = execute(tmp_path)
    assert proof.verdict.decision == "BLOCK"
    assert proof.proofmerge_version == __version__
    assert proof.run_id == evidence.name
    assert (proof.base_sha, proof.head_sha, proof.diff_sha256) == (
        context.base_sha, context.head_sha, context.diff_sha256
    )
    assert proof.model_usage == ModelUsage(calls=3, prompt_tokens=22, completion_tokens=14)
    assert client.calls == 3
    assert proof.findings[0].verified
    assert [e.outcome for e in proof.experiments[0].executions] == ["PASS"] * 3 + ["FAIL"] * 3
    assert snapshots == {p: p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    assert all(b"test-secret-must-stay-out" not in data for data in snapshots.values())
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("call", [1, 2, 3])
def test_api_failure_keeps_completed_stages_and_writes_review(tmp_path, call):
    proof, evidence, _, _ = execute(tmp_path, replies=Replies(fail=call))
    assert proof.verdict.decision == "REVIEW"
    assert "scripted API unavailable" in proof.verdict.reasons
    assert len(proof.intent.claims) == (1 if call == 1 else 2)
    assert len(proof.hypotheses.hypotheses) == (1 if call == 3 else 0)
    assert proof.experiments == []
    assert_bundle(proof, evidence)


def test_no_intent_regression_is_review(tmp_path):
    proof, evidence, client, _ = execute(tmp_path, replies=Replies(claim="C0"), intent=None)
    assert [c.id for c in proof.intent.claims] == ["C0"]
    assert proof.findings[0].classification == "REGRESSION"
    assert proof.verdict.decision == "REVIEW"
    assert client.calls == 2
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("error", [SandboxError("sandbox unavailable"), OSError("runner failed")])
def test_runner_exception_is_recorded(tmp_path, error):
    def fail(*args):
        raise error

    proof, evidence, _, _ = execute(tmp_path, runner=fail)
    assert proof.verdict.decision == "REVIEW"
    assert str(error) in proof.verdict.reasons
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("stage", ["static", "dynamic"])
@pytest.mark.parametrize("error_type", [TamperError, SandboxError, ContextError, OSError])
def test_tamper_failure_reviews_even_verified_regression(tmp_path, monkeypatch, stage, error_type):
    finding = TamperFinding(
        test_path="test_calc.py", test_name=None, kind="MODIFIED",
        base_test_on_head_outcome=None,
    )

    def fail(*args, **kwargs):
        raise error_type("tamper stage unavailable")

    monkeypatch.setattr(pipeline, "find_tamper", fail if stage == "static" else lambda *a: [finding])
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", fail)
    proof, evidence, _, _ = execute(tmp_path)
    assert proof.findings[0].verified
    assert proof.verdict.decision == "REVIEW"
    assert "tamper stage unavailable" in proof.verdict.reasons
    assert proof.tamper_findings == ([] if stage == "static" else [finding])
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("outcome", ["ERROR", "TIMEOUT"])
def test_failed_tamper_execution_reviews_verified_regression(tmp_path, monkeypatch, outcome):
    from test_findings import experiment

    tamper_experiment = experiment(["PASS"] * 3, [outcome] * 3)
    tamper_experiment.hypothesis_id = "T1"
    finding = TamperFinding(
        test_path="test_calc.py", test_name=None, kind="MODIFIED",
        base_test_on_head_outcome=outcome,
    )
    monkeypatch.setattr(pipeline, "find_tamper", lambda *a: [finding])
    monkeypatch.setattr(
        pipeline, "run_base_tests_on_head", lambda *a, **k: ([finding], [tamper_experiment])
    )
    proof, evidence, _, _ = execute(tmp_path)
    assert proof.findings[0].verified
    assert proof.verdict.decision == "REVIEW"
    assert any(outcome in reason for reason in proof.verdict.reasons)
    # The hand-built tamper experiment has no recorded files; check the proof and manifest.
    assert Proof.model_validate_json((evidence / "proof.json").read_text()) == proof
    assert verify_manifest(evidence) == []


def test_api_failure_reviews_successful_tamper_fail(tmp_path, monkeypatch):
    finding = TamperFinding(
        test_path="test_calc.py", test_name=None, kind="MODIFIED",
        base_test_on_head_outcome="FAIL",
    )
    monkeypatch.setattr(pipeline, "find_tamper", lambda *a: [finding])
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", lambda *a, **k: ([finding], []))
    proof, evidence, _, _ = execute(tmp_path, replies=Replies(fail=2))
    assert proof.verdict.decision == "REVIEW"
    assert proof.tamper_findings == [finding]
    assert "scripted API unavailable" in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_tamper_experiments_are_recorded_but_not_normalized(tmp_path):
    repo, _ = make_repo(tmp_path)
    (repo / "test_calc.py").write_text(PROBE)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "add test")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "test_calc.py").unlink()
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "remove test")
    client = MetaClient(FakeCompletions(content='{"hypotheses": []}'))
    proof = run_verify(
        repo, base, "HEAD", None, tmp_path / "ev", client,
        runner=lambda *a: (0, b"", b""), digest="fake",
    )
    assert proof.experiments[0].hypothesis_id == "T1"
    assert proof.findings == []
    assert all(t.base_test_on_head_outcome == "PASS" for t in proof.tamper_findings)
    assert proof.verdict.decision == "REVIEW"
    assert_bundle(proof, tmp_path / "ev")


@pytest.mark.parametrize("problem", ["bad-ref", "huge-diff", "existing-dir"])
def test_preflight_propagates_without_model_or_evidence_writes(tmp_path, monkeypatch, problem):
    repo, context = make_repo(tmp_path)
    evidence = tmp_path / "ev"
    base = context.base_sha
    if problem == "bad-ref":
        base = "missing"
    elif problem == "huge-diff":
        monkeypatch.setattr(pipeline, "get_context", lambda *a: context.model_copy(
            update={"diff_text": "x" * (MAX_DIFF_CHARS + 1)}
        ))
    else:
        evidence.mkdir()
        (evidence / "keep").write_text("untouched")
    client = MetaClient(Replies())
    with pytest.raises((ContextError, ValueError, FileExistsError)):
        run_verify(repo, base, context.head_sha, INTENT, evidence, client)
    assert client.calls == 0
    assert not evidence.exists() or list(evidence.iterdir()) == [evidence / "keep"]


def arguments(repo, evidence, base="HEAD~1"):
    return ["verify", str(repo), "--base", base, "--head", "HEAD", "--evidence-dir", str(evidence)]


@pytest.mark.parametrize(("decision", "status"), [("PASS", 0), ("REVIEW", 0), ("BLOCK", 2)])
def test_cli_verdict_exit_codes_and_usage(tmp_path, monkeypatch, decision, status):
    repo, _ = make_repo(tmp_path)
    proof = Proof.model_validate_json((Path(__file__).parent / "fixtures/sample_proof_block.json").read_text())
    proof.verdict.decision = decision
    seen = {}

    def run(*args):
        seen["args"] = args
        return proof

    monkeypatch.setattr(cli, "MetaClient", lambda **kwargs: seen.setdefault("client", MetaClient(Replies(), **kwargs)))
    monkeypatch.setattr(cli, "run_verify", run)
    intent_file = tmp_path / "intent.md"
    intent_file.write_text(INTENT)
    evidence = tmp_path / "ev"
    result = CliRunner().invoke(cli.app, arguments(repo, evidence) + ["--intent-file", str(intent_file)])
    assert result.exit_code == status, result.output
    assert f"verdict: {decision}" in result.output
    assert str(evidence / "report.html") in result.output
    assert all(reason in result.output for reason in proof.verdict.reasons)
    assert "calls=0 tokens: prompt=0 completion=0" in result.output
    assert seen["args"][3] == INTENT
    assert seen["client"].max_calls == 2 + MAX_HYPOTHESES * MAX_BASE_RUNS


@pytest.mark.parametrize("error", [ContextError("bad ref"), ValueError("refused diff"), FileExistsError("exists")])
def test_cli_could_not_run_exit_one(tmp_path, monkeypatch, error):
    repo, _ = make_repo(tmp_path)
    monkeypatch.setattr(cli, "MetaClient", lambda **k: MetaClient(Replies(), **k))

    def fail(*a):
        raise error

    monkeypatch.setattr(cli, "run_verify", fail)
    result = CliRunner().invoke(cli.app, arguments(repo, tmp_path / "ev"))
    assert result.exit_code == 1
    assert str(error) in result.output


def test_cli_no_changes_does_not_create_client_or_write(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)

    def unexpected(**kwargs):
        pytest.fail("no client should be constructed")

    monkeypatch.setattr(cli, "MetaClient", unexpected)
    evidence = tmp_path / "ev"
    result = CliRunner().invoke(cli.app, arguments(repo, evidence, base="HEAD"))
    assert result.exit_code == 0
    assert "no changes between base and head; nothing to check" in result.output
    assert not evidence.exists()


def test_cli_missing_credentials_writes_review(tmp_path, monkeypatch):
    repo, _ = make_repo(tmp_path)
    # Keep this test offline even if client initialization unexpectedly succeeds.
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", lambda *a, **k: ([], []))
    evidence = tmp_path / "ev"
    result = CliRunner().invoke(cli.app, arguments(repo, evidence))
    assert result.exit_code == 0, result.output
    proof = Proof.model_validate_json((evidence / "proof.json").read_text())
    assert proof.verdict.decision == "REVIEW"
    assert any("MODEL_API_KEY is not set" in r for r in proof.verdict.reasons)
    assert_bundle(proof, evidence)


@needs_docker
def test_docker_real_demo_end_to_end(demo_repo, tmp_path):
    probe = (ROOT / "demo/ground_truth/tenant-cache-regression/test_cross_tenant.py").read_text()
    replies = Replies(location="src/demo_shop/pricing.py::price_for", probe=probe)
    proof = run_verify(
        demo_repo, "main", "candidate/tenant-cache-regression", INTENT,
        tmp_path / "docker-evidence", MetaClient(replies),
    )
    assert proof.verdict.decision == "BLOCK"
    assert [e.outcome for e in proof.experiments[0].executions] == ["PASS"] * 3 + ["FAIL"] * 3
    assert_bundle(proof, tmp_path / "docker-evidence")
