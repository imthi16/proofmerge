"""LangGraph orchestration: same stages, same evidence, verdict only from decide()."""

import httpx2
import openai
import pytest
from test_pipeline import INTENT, Replies, assert_bundle
from test_probes import fake_runner, make_repo

from proofmerge import graph, pipeline
from proofmerge.evidence import Proof
from proofmerge.meta import MetaClient, MetaError
from proofmerge.pipeline import STAGES, run_verify
from proofmerge.sandbox import SandboxError

ALL_NODES = [name for name, _ in STAGES]


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(graph, "RETRY_INTERVAL_SECONDS", 0.0)
    monkeypatch.delenv("PROOFMERGE_ORCHESTRATOR", raising=False)


def http_error(status: int) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://api.meta.ai/v1/chat/completions")
    kind = {429: openai.RateLimitError, 503: openai.InternalServerError}.get(
        status, openai.BadRequestError
    )
    return kind("upstream says no", response=httpx2.Response(status, request=request), body=None)


class Flaky(Replies):
    """Replies whose listed call numbers fail with an SDK error first."""

    def __init__(self, failures: dict[int, Exception], **kwargs):
        super().__init__(**kwargs)
        self.failures = failures
        self.attempts = 0

    def create(self, **kwargs):
        self.attempts += 1
        if self.attempts in self.failures:
            raise self.failures[self.attempts]
        return super().create(**kwargs)


def verify(repo, context, evidence, engine, replies=None, runner=None, intent=INTENT):
    client = MetaClient(replies or Replies())
    proof = run_verify(
        repo, context.base_sha, context.head_sha, intent, evidence, client,
        runner=runner or fake_runner([]), digest="sha256:fake", orchestrator=engine,
    )
    return proof, client


def comparable(proof: Proof, evidence) -> str:
    # Reproducer commands name the absolute evidence path, which differs per run.
    return proof.model_dump_json(exclude={"orchestration"}).replace(str(evidence), "<ev>")


def evidence_files(evidence) -> dict:
    # proof.json and manifest.json carry the orchestration record; all else must match.
    skip = {"proof.json", "manifest.json"}
    return {
        str(p.relative_to(evidence)): p.read_bytes().replace(str(evidence).encode(), b"<ev>")
        for p in evidence.rglob("*") if p.is_file() and p.name not in skip
    }


def pass_runner(*args):
    return 0, b"", b""


SCENARIOS = {
    "block-regression": {"replies": Replies},
    "pass-no-difference": {"replies": Replies, "runner": lambda: pass_runner},
    "review-intent-api-failure": {"replies": lambda: Replies(fail=1)},
    "review-hypotheses-api-failure": {"replies": lambda: Replies(fail=2)},
    "review-probe-api-failure": {"replies": lambda: Replies(fail=3)},
    "block-despite-wonder-failure": {"replies": lambda: Replies(fail=4)},
    "review-no-intent": {"replies": lambda: Replies(claim="C0"), "intent": None},
    "review-wonder-mismatch": {"replies": lambda: Replies(wonder_results=[
        {"hypothesis_id": "H1", "tests_hypothesis": False, "reason": "wrong behavior"}
    ])},
    "review-runner-failure": {
        "replies": Replies, "runner": lambda: _raise(SandboxError("sandbox unavailable"))
    },
}
EXPECTED = {
    "block-regression": "BLOCK",
    "pass-no-difference": "PASS",
    "review-intent-api-failure": "REVIEW",
    "review-hypotheses-api-failure": "REVIEW",
    "review-probe-api-failure": "REVIEW",
    "block-despite-wonder-failure": "BLOCK",
    "review-no-intent": "REVIEW",
    "review-wonder-mismatch": "REVIEW",
    "review-runner-failure": "REVIEW",
}


def _raise(error):
    def run(*args):
        raise error

    return run


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_graph_and_legacy_produce_identical_evidence(tmp_path, name):
    spec = SCENARIOS[name]
    repo, context = make_repo(tmp_path)
    results = {}
    for engine in ("legacy", "langgraph"):
        # Same final directory name, so run_id and report paths match.
        evidence = tmp_path / engine / "ev"
        runner = spec["runner"]() if "runner" in spec else None
        proof, client = verify(
            repo, context, evidence, engine, replies=spec["replies"](), runner=runner,
            intent=spec.get("intent", INTENT),
        )
        assert_bundle(proof, evidence)
        assert proof.orchestration.engine == engine
        results[engine] = (comparable(proof, evidence), evidence_files(evidence), client.calls)
        decisions = proof.verdict.decision
    assert results["legacy"] == results["langgraph"]
    assert decisions == EXPECTED[name]


def test_graph_records_nodes_and_skips_stages_without_inputs(tmp_path):
    repo, context = make_repo(tmp_path)
    proof, _ = verify(repo, context, tmp_path / "ok", "langgraph")
    assert proof.orchestration.nodes == ALL_NODES
    assert proof.orchestration.langgraph_version
    assert proof.orchestration.retries == []

    proof, _ = verify(repo, context, tmp_path / "no-intent", "langgraph", Replies(fail=1))
    assert "generate_hypotheses" not in proof.orchestration.nodes
    assert "generate_execute_probes" not in proof.orchestration.nodes
    assert proof.orchestration.nodes[-3:] == [
        "normalize_findings", "deterministic_decide", "write_evidence"
    ]


def test_legacy_record_names_engine_only(tmp_path):
    repo, context = make_repo(tmp_path)
    proof, _ = verify(repo, context, tmp_path / "ev", "legacy")
    assert proof.orchestration.model_dump() == {
        "engine": "legacy", "langgraph_version": None, "nodes": [], "retries": []
    }


@pytest.mark.parametrize("call", [1, 2, 4, 5])
@pytest.mark.parametrize("status", [429, 503])
def test_graph_retries_transient_model_failure_and_recovers(tmp_path, call, status):
    repo, context = make_repo(tmp_path)
    clean, _ = verify(repo, context, tmp_path / "clean" / "ev", "legacy")
    proof, client = verify(
        repo, context, tmp_path / "graph" / "ev", "langgraph", Flaky({call: http_error(status)})
    )
    assert proof.verdict == clean.verdict
    assert proof.verdict.decision == "BLOCK"
    assert len(proof.orchestration.retries) == 1
    assert f"HTTP {status}" in proof.orchestration.retries[0]
    # The failed attempt still counts against the run's model budget.
    assert client.calls == clean.model_usage.calls + 1

    legacy, _ = verify(
        repo, context, tmp_path / "legacy" / "ev", "legacy", Flaky({call: http_error(status)})
    )
    assert legacy.verdict.decision != "PASS"
    assert f"Meta API request failed (HTTP {status})" in legacy.verdict.reasons


def test_exhausted_transient_retries_become_review(tmp_path):
    repo, context = make_repo(tmp_path)
    failures = {n: http_error(503) for n in range(1, graph.MAX_MODEL_ATTEMPTS + 1)}
    proof, client = verify(repo, context, tmp_path / "ev", "langgraph", Flaky(failures))
    assert proof.verdict.decision == "REVIEW"
    assert "Meta API request failed (HTTP 503)" in proof.verdict.reasons
    assert len(proof.orchestration.retries) == graph.MAX_MODEL_ATTEMPTS - 1
    assert proof.experiments == []
    assert client.calls >= graph.MAX_MODEL_ATTEMPTS


def test_non_transient_failure_is_not_retried(tmp_path):
    repo, context = make_repo(tmp_path)
    proof, _ = verify(repo, context, tmp_path / "ev", "langgraph", Flaky({1: http_error(400)}))
    assert proof.verdict.decision == "REVIEW"
    assert proof.orchestration.retries == []
    assert "Meta API request failed (HTTP 400)" in proof.verdict.reasons


def test_probe_node_is_never_retried(tmp_path):
    # Call 3 is the probe-writing call inside the sandbox stage: no graph retry.
    repo, context = make_repo(tmp_path)
    runs: list[str] = []
    proof, _ = verify(
        repo, context, tmp_path / "ev", "langgraph", Flaky({3: http_error(503)}),
        runner=fake_runner(runs),
    )
    assert proof.orchestration.retries == []
    assert proof.orchestration.nodes.count("generate_execute_probes") == 1
    assert proof.verdict.decision == "REVIEW"
    assert runs == []


def test_sandbox_runs_are_not_repeated_by_the_graph(tmp_path):
    repo, context = make_repo(tmp_path)
    runs = {"legacy": [], "langgraph": []}
    for engine, calls in runs.items():
        proof, _ = verify(repo, context, tmp_path / engine, engine, runner=fake_runner(calls))
        assert [e.attempt for e in proof.experiments[0].executions] == [1, 2, 3] * 2
    # One draft check on base, then the three recorded attempts per revision.
    assert runs["langgraph"] == runs["legacy"] == ["base"] * 4 + ["head"] * 3


@pytest.mark.parametrize("engine", ["legacy", "langgraph"])
def test_verdict_comes_only_from_decide(tmp_path, monkeypatch, engine):
    calls = []
    real = pipeline.decide

    def spy(*args, **kwargs):
        calls.append(kwargs["errors"])
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "decide", spy)
    repo, context = make_repo(tmp_path)
    proof, _ = verify(repo, context, tmp_path / "ev", engine, Replies(fail=4))
    assert len(calls) == 1
    assert calls[0] == ["scripted API unavailable"]
    assert proof.verdict.decision == "BLOCK"


def test_unknown_orchestrator_and_env_selection(tmp_path, monkeypatch):
    repo, context = make_repo(tmp_path)
    with pytest.raises(ValueError, match="unknown orchestrator"):
        verify(repo, context, tmp_path / "bad", "dag")
    assert not (tmp_path / "bad").exists()
    monkeypatch.setenv("PROOFMERGE_ORCHESTRATOR", "langgraph")
    proof, _ = verify(repo, context, tmp_path / "env", None)
    assert proof.orchestration.engine == "langgraph"


def test_missing_langgraph_is_a_clear_error(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "proofmerge.graph", None)
    repo, context = make_repo(tmp_path)
    with pytest.raises(ValueError, match=r"proofmerge\[graph\]"):
        verify(repo, context, tmp_path / "ev", "langgraph")
    assert not (tmp_path / "ev").exists()


def test_meta_error_transient_flag(monkeypatch):
    from test_meta import FakeCompletions, ask

    for status, transient in ((429, True), (503, True), (400, False)):
        with pytest.raises(MetaError) as caught:
            ask(MetaClient(FakeCompletions(error=http_error(status))))
        assert caught.value.transient is transient
    request = httpx2.Request("POST", "https://api.meta.ai/v1/chat/completions")
    with pytest.raises(MetaError) as caught:
        ask(MetaClient(FakeCompletions(error=openai.APIConnectionError(request=request))))
    assert caught.value.transient
    with pytest.raises(MetaError) as caught:
        ask(MetaClient(FakeCompletions(content="{}")))
    assert not caught.value.transient


def test_cli_orchestrator_flag(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from proofmerge import cli

    repo, _ = make_repo(tmp_path)
    # Intent extraction fails, so no probe reaches a sandbox; tamper runs are faked.
    monkeypatch.setattr(cli, "MetaClient", lambda **k: MetaClient(Replies(fail=1), **k))
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", lambda *a, **k: ([], []))
    base = ["verify", str(repo), "--base", "HEAD~1", "--head", "HEAD"]

    bad = CliRunner().invoke(cli.app, [*base, "--evidence-dir", str(tmp_path / "bad"),
                                       "--orchestrator", "dag"])
    assert bad.exit_code == 1
    assert "unknown orchestrator" in bad.output
    assert not (tmp_path / "bad").exists()

    evidence = tmp_path / "ev"
    result = CliRunner().invoke(
        cli.app, [*base, "--evidence-dir", str(evidence), "--orchestrator", "langgraph"]
    )
    assert result.exit_code == 0, result.output
    proof = Proof.model_validate_json((evidence / "proof.json").read_text())
    assert proof.orchestration.engine == "langgraph"
    assert proof.orchestration.nodes[-1] == "write_evidence"
    assert proof.verdict.decision == "REVIEW"
