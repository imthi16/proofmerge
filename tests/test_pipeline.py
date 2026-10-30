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
from proofmerge.meta import WONDER_MODEL, MetaClient, MetaError
from proofmerge.pipeline import run_verify
from proofmerge.probes import MAX_BASE_RUNS
from proofmerge.sandbox import SandboxError
from proofmerge.tamper import TamperError

pytest_plugins = ["test_sandbox"]

INTENT = "Returned prices must not change."
PROBE = "# GOOD\nfrom calc import double\ndef test_double():\n    assert double(3) == 6\n"


class Replies:
    """Script Spark stages and independent Wonder checks around probe completions."""

    def __init__(self, *, claim="C1", location="calc.py::double", fail=None, probe=PROBE,
                 wonder_claims=None, wonder_results=None):
        self.wonder_claims = wonder_claims
        self.wonder_results = wonder_results
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
        if kwargs["model"] == WONDER_MODEL:
            data = json.loads(kwargs["messages"][1]["content"])
            if "claims" in data:
                reply = {"claims": [
                    {"id": c["id"], "kind": "PRESERVE"} for c in data["claims"]
                ]}
            else:
                reply = {"results": [
                    {"hypothesis_id": p["hypothesis_id"], "tests_hypothesis": True,
                     "reason": "matches"} for p in data["probes"]
                ]}
            if "claims" in data and self.wonder_claims is not None:
                reply = {"claims": self.wonder_claims}
            if "probes" in data and self.wonder_results is not None:
                reply = {"results": self.wonder_results}
            response = FakeCompletions(content=json.dumps(reply)).create(**kwargs)
            response.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
            return response
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
    assert proof.model_usage == ModelUsage(calls=5, prompt_tokens=44, completion_tokens=28)
    assert client.calls == 5
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
    assert client.calls == 3
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
def test_tamper_stage_failure_keeps_verified_regression_block(tmp_path, monkeypatch, stage, error_type):
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
    assert proof.verdict.decision == "BLOCK"
    assert "tamper stage unavailable" in proof.verdict.reasons
    assert proof.tamper_findings == ([] if stage == "static" else [finding])
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("outcome", ["ERROR", "TIMEOUT"])
def test_failed_tamper_execution_keeps_verified_regression_block(tmp_path, monkeypatch, outcome):
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
    assert proof.verdict.decision == "BLOCK"
    assert any(outcome in reason for reason in proof.verdict.reasons)
    # The hand-built tamper experiment has no recorded files; check the proof and manifest.
    assert Proof.model_validate_json((evidence / "proof.json").read_text()) == proof
    assert verify_manifest(evidence) == []


def test_later_probe_failure_keeps_completed_regression(tmp_path, monkeypatch):
    import hashlib

    from test_findings import experiment

    from proofmerge import probes as probes_mod

    replies = Replies()
    replies.replies[1]["hypotheses"].append(hypothesis("C1", ["calc.py::double"]))
    calls = {"n": 0}

    def write_probe(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise MetaError("second probe failed")
        return probes_mod.Probe(
            hypothesis_id=args[3].id, test_source="assert True\n", drafts=1, passed_on_base=True
        )

    def run_experiment(*args, **kwargs):
        evidence, rel = args[5], "probes/test_h1.py"
        target = evidence / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = b"assert True\n"
        target.write_bytes(data)
        saved = experiment(["PASS"] * 3, ["FAIL"] * 3, hypothesis_id=args[3])
        saved.test_source_path = rel
        saved.test_source_sha256 = hashlib.sha256(data).hexdigest()
        return saved

    monkeypatch.setattr(probes_mod, "write_probe", write_probe)
    monkeypatch.setattr(probes_mod, "run_experiment", run_experiment)
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert [item.hypothesis_id for item in proof.experiments] == ["H1"]
    assert proof.findings[0].verified
    assert proof.verdict.decision == "BLOCK"
    assert "second probe failed" in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_later_tamper_run_keeps_earlier_fail(tmp_path, monkeypatch):
    import hashlib

    from test_findings import experiment

    from proofmerge import tamper as tamper_mod

    findings = [
        TamperFinding(
            test_path=path, test_name=None, kind="MODIFIED", base_test_on_head_outcome=None
        )
        for path in ("a_test.py", "b_test.py")
    ]
    monkeypatch.setattr(pipeline, "find_tamper", lambda *args: findings)

    def fake_git(repo, *args, error):
        return b"blob\0" if args[0] == "ls-tree" else b"#\n"

    calls = {"n": 0}

    def run_experiment(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SandboxError("later tamper run failed")
        evidence, rel = args[5], "probes/test_t1.py"
        data = b"assert True\n"
        target = evidence / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        saved = experiment(["PASS"] * 3, ["FAIL"] * 3, hypothesis_id=args[3])
        saved.test_source_path = rel
        saved.test_source_sha256 = hashlib.sha256(data).hexdigest()
        return saved

    monkeypatch.setattr(tamper_mod, "_git", fake_git)
    monkeypatch.setattr(tamper_mod, "run_experiment", run_experiment)
    proof, evidence, _, _ = execute(tmp_path)
    assert proof.verdict.decision == "BLOCK"
    assert [item.base_test_on_head_outcome for item in proof.tamper_findings] == ["FAIL", "ERROR"]
    assert [item.hypothesis_id for item in proof.experiments if item.hypothesis_id.startswith("T")] == ["T1"]
    assert "later tamper run failed" in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_intent_extraction_failure_does_not_block_on_tamper(tmp_path, monkeypatch):
    finding = TamperFinding(
        test_path="test_calc.py", test_name=None, kind="MODIFIED",
        base_test_on_head_outcome="FAIL",
    )
    monkeypatch.setattr(pipeline, "find_tamper", lambda *args: [finding])
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", lambda *args, **kwargs: ([finding], []))
    proof, evidence, _, _ = execute(tmp_path, replies=Replies(fail=1))
    assert proof.verdict.decision == "REVIEW"
    assert any("intent extraction failed" in reason for reason in proof.verdict.reasons)
    assert "scripted API unavailable" in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_api_failure_keeps_tamper_fail_block(tmp_path, monkeypatch):
    finding = TamperFinding(
        test_path="test_calc.py", test_name=None, kind="MODIFIED",
        base_test_on_head_outcome="FAIL",
    )
    monkeypatch.setattr(pipeline, "find_tamper", lambda *a: [finding])
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", lambda *a, **k: ([finding], []))
    proof, evidence, _, _ = execute(tmp_path, replies=Replies(fail=2))
    assert proof.verdict.decision == "BLOCK"
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
    assert seen["client"].max_calls == 4 + MAX_HYPOTHESES * MAX_BASE_RUNS


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


@pytest.mark.parametrize("signal", ["flipped", "omitted-claim", "mismatch", "omitted-probe"])
def test_wonder_disagreement_reaches_verdict_proof_and_report(tmp_path, signal):
    kwargs = {
        "flipped": {"wonder_claims": [{"id": "C1", "kind": "CHANGE"}]},
        "omitted-claim": {"wonder_claims": []},
        "mismatch": {"wonder_results": [
            {"hypothesis_id": "H1", "tests_hypothesis": False, "reason": "wrong behavior"}
        ]},
        "omitted-probe": {"wonder_results": []},
    }[signal]
    proof, evidence, client, _ = execute(tmp_path, replies=Replies(**kwargs))
    assert proof.findings[0].verified
    assert proof.verdict.decision == "REVIEW"
    claim_signal = signal in {"flipped", "omitted-claim"}
    assert proof.contested_claim_ids == (["C1"] if claim_signal else [])
    assert proof.mismatched_probe_ids == ([] if claim_signal else ["H1"])
    assert client.calls == 5
    annotation = ("contested by Muse Wonder" if claim_signal
                  else "Muse Wonder: probe may not test this hypothesis")
    assert annotation in (evidence / "report.html").read_text()
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("call", [4, 5])
@pytest.mark.parametrize("regression", [False, True])
def test_wonder_meta_error_records_reason_writes_proof_and_preserves_block(tmp_path, call, regression):
    runner = fake_runner([]) if regression else lambda *args: (0, b"", b"")
    proof, evidence, _, _ = execute(tmp_path, replies=Replies(fail=call), runner=runner)
    assert proof.verdict.decision == ("BLOCK" if regression else "REVIEW")
    assert "scripted API unavailable" in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_successful_claim_check_is_retained_when_probe_check_fails(tmp_path):
    proof, evidence, _, _ = execute(tmp_path, replies=Replies(
        fail=5, wonder_claims=[{"id": "C1", "kind": "CHANGE"}],
    ))
    assert proof.verdict.decision == "REVIEW"
    assert proof.contested_claim_ids == ["C1"]
    assert "scripted API unavailable" in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_wonder_ids_are_sorted_in_proof_and_other_finding_still_blocks(tmp_path):
    replies = Replies(wonder_results=[
        {"hypothesis_id": id, "tests_hypothesis": False, "reason": "wrong behavior"}
        for id in ("H2", "H1")
    ])
    replies.replies[1]["hypotheses"].extend([
        hypothesis("C1", ["calc.py::double"]), hypothesis("C0", ["calc.py::double"]),
    ])
    replies.probes = ScriptedCompletions(PROBE, PROBE, PROBE)
    # H3 is independently confirmed while H1/H2 are mismatched.
    replies.wonder_results.append(
        {"hypothesis_id": "H3", "tests_hypothesis": True, "reason": "matches"}
    )
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert proof.verdict.decision == "BLOCK"
    assert proof.mismatched_probe_ids == ["H1", "H2"]
    assert "H1 needs review because its probe may not test its hypothesis." in proof.verdict.reasons
    assert "H2 needs review because its probe may not test its hypothesis." in proof.verdict.reasons
    assert_bundle(proof, evidence)


def test_contested_claim_ids_are_sorted_in_proof(tmp_path):
    replies = Replies(wonder_claims=[
        {"id": id, "kind": "CHANGE"} for id in ("C3", "C1", "C2")
    ])
    replies.replies[0]["claims"] *= 3
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert proof.contested_claim_ids == ["C1", "C2", "C3"]
    assert proof.verdict.decision == "REVIEW"
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("invalid_stage", ["claims", "probes"])
def test_invalid_wonder_reply_is_recorded_without_losing_evidence(tmp_path, invalid_stage):
    replies = (Replies(wonder_claims=[{"id": "C1", "kind": "invalid"}])
               if invalid_stage == "claims" else Replies(wonder_results=[
                   {"hypothesis_id": "H1", "tests_hypothesis": "false", "reason": "invalid"}
               ]))
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert proof.verdict.decision == "BLOCK"
    assert any("model reply did not match Checked" in reason for reason in proof.verdict.reasons)
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("results", [[], [
    {"hypothesis_id": "H1", "tests_hypothesis": False, "reason": "unrelated assertions"}
]])
def test_passing_probe_rejected_by_wonder_requires_review(tmp_path, results):
    proof, evidence, _, _ = execute(
        tmp_path, replies=Replies(wonder_results=results), runner=lambda *args: (0, b"", b""),
    )
    assert proof.findings[0].classification == "NO_DIFFERENCE"
    assert proof.mismatched_probe_ids == ["H1"]
    assert proof.verdict.decision == "REVIEW"
    assert "H1 needs review because its probe may not test its hypothesis." in proof.verdict.reasons
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("failure", ["api", "schema"])
@pytest.mark.parametrize("other_regression", [False, True])
def test_claim_check_failure_does_not_skip_probe_check(tmp_path, failure, other_regression):
    replies = Replies(
        fail=(5 if other_regression else 4) if failure == "api" else None,
        wonder_claims=[{"id": "C1", "kind": "invalid"}] if failure == "schema" else None,
        wonder_results=[
            {"hypothesis_id": "H1", "tests_hypothesis": False, "reason": "wrong behavior"}
        ],
    )
    if other_regression:
        replies.replies[1]["hypotheses"].append(hypothesis("C0", ["calc.py::double"]))
        replies.probes = ScriptedCompletions(PROBE, PROBE)
        replies.wonder_results.append(
            {"hypothesis_id": "H2", "tests_hypothesis": True, "reason": "matches"}
        )
    proof, evidence, client, _ = execute(tmp_path, replies=replies)
    assert proof.mismatched_probe_ids == ["H1"]
    assert proof.contested_claim_ids == []
    assert client.calls == (6 if other_regression else 5)
    assert proof.verdict.decision == ("BLOCK" if other_regression else "REVIEW")
    error_text = "scripted API unavailable" if failure == "api" else "model reply did not match"
    assert any(error_text in reason for reason in proof.verdict.reasons)
    assert "H1 needs review because its probe may not test its hypothesis." in proof.verdict.reasons
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("intent", [None, "", " \n\t ", "Improve price calculation."])
def test_c0_only_intent_text_controls_regression_verdict(tmp_path, intent):
    replies = Replies(claim="C0")
    if intent and intent.strip():
        replies.replies.insert(0, {"claims": []})
    proof, evidence, _, _ = execute(tmp_path, replies=replies, intent=intent)
    assert proof.intent.claims[0].id == "C0"
    assert len(proof.intent.claims) == 1
    assert proof.findings[0].verified
    assert proof.verdict.decision == ("BLOCK" if intent and intent.strip() else "REVIEW")
    assert_bundle(proof, evidence)


def test_all_proposed_claims_rejected_keeps_c0_regression_review(tmp_path):
    replies = Replies(claim="C0")
    replies.replies.insert(0, {"claims": [{
        "kind": "PRESERVE", "statement": "invented", "source_quote": "not in intent text",
    }]})
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert proof.intent.rejected
    assert proof.findings[0].verified
    assert proof.verdict.decision == "REVIEW"
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("explanation, expected", [
    ("C1", "REVIEW"), ("C0", "BLOCK"), ("C99", "BLOCK"), (None, "BLOCK"),
])
@pytest.mark.parametrize("claim", ["C0", "C1"])
def test_explained_change_reaches_verdict_proof_and_report(tmp_path, explanation, expected, claim):
    intent = "Use the new customer field for promotion eligibility."
    replies = Replies(claim=claim, wonder_claims=[{"id": "C1", "kind": "CHANGE"}],
                      wonder_results=[{
                          "hypothesis_id": "H1", "tests_hypothesis": True,
                          "reason": "explicit requested behavior", "explained_by_change": explanation,
                      }])
    if claim == "C1":
        replies.replies.pop(0)
    replies.replies.insert(0, {"claims": [{
        "kind": "CHANGE", "statement": "new field", "source_quote": intent,
    }]})
    proof, evidence, _, _ = execute(tmp_path, replies=replies, intent=intent)
    assert proof.findings[0].verified
    assert proof.verdict.decision == expected
    assert proof.explained_probe_ids == (["H1"] if explanation == "C1" else [])
    assert proof.explained_probe_claim_ids == ({"H1": "C1"} if explanation == "C1" else {})
    annotation = "Muse Wonder: may be the intended change C1"
    assert (annotation in (evidence / "report.html").read_text()) == (explanation == "C1")
    if explanation == "C1":
        assert "H1 may be the intended change C1." in proof.verdict.reasons
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("failure", ["api", "schema"])
def test_change_citation_keeps_review_when_wonder_probe_check_fails(tmp_path, failure):
    replies = Replies(
        fail=5 if failure == "api" else None,
        wonder_claims=[{"id": "C1", "kind": "CHANGE"}],
        wonder_results=[{"hypothesis_id": "H1", "tests_hypothesis": "false", "reason": "bad"}],
    )
    replies.replies[0]["claims"][0]["kind"] = "CHANGE"
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert proof.findings[0].verified
    assert proof.contested_claim_ids == []
    assert proof.verdict.decision == "REVIEW"
    assert "H1 needs review because C1 is a CHANGE claim." in proof.verdict.reasons
    error = "scripted API unavailable" if failure == "api" else "model reply did not match"
    assert any(error in reason for reason in proof.verdict.reasons)
    assert_bundle(proof, evidence)


def test_change_citation_accepts_a_different_change_explanation(tmp_path):
    replies = Replies(
        wonder_claims=[{"id": id, "kind": "CHANGE"} for id in ("C1", "C2")],
        wonder_results=[{
            "hypothesis_id": "H1", "tests_hypothesis": True,
            "reason": "another change", "explained_by_change": "C2",
        }],
    )
    change = replies.replies[0]["claims"][0]
    change["kind"] = "CHANGE"
    replies.replies[0]["claims"].append(change.copy())
    proof, evidence, _, _ = execute(tmp_path, replies=replies)
    assert proof.findings[0].verified
    assert proof.contested_claim_ids == []
    assert proof.explained_probe_claim_ids == {"H1": "C2"}
    assert proof.verdict.decision == "REVIEW"
    assert proof.verdict.reasons == ["H1 may be the intended change C2."]
    assert_bundle(proof, evidence)


@pytest.mark.parametrize("failure", ["api", "schema"])
def test_change_citation_uses_successful_probe_check_after_claim_check_error(tmp_path, failure):
    replies = Replies(
        fail=4 if failure == "api" else None,
        wonder_claims=[{"id": "C1", "kind": "invalid"}],
    )
    replies.replies[0]["claims"][0]["kind"] = "CHANGE"
    proof, evidence, client, _ = execute(tmp_path, replies=replies)
    assert client.calls == 5
    assert proof.findings[0].verified
    assert proof.verdict.decision == "BLOCK"
    assert "PRESERVE claim C0" in proof.verdict.reasons[0]
    error = "scripted API unavailable" if failure == "api" else "model reply did not match"
    assert any(error in reason for reason in proof.verdict.reasons)
    assert_bundle(proof, evidence)
