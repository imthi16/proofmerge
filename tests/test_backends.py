import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.cli_backend import CliCompletions
from proofmerge.evidence import ModelUsage, Proof
from proofmerge.meta import SPARK_MODEL, WONDER_MODEL, MetaClient, MetaError
from proofmerge.report import render_html
from tests.test_meta import Answer, FakeCompletions, ask

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("PROOFMERGE_BACKEND", "PROOFMERGE_BASE_URL", "PROOFMERGE_SPARK_MODEL",
                 "PROOFMERGE_WONDER_MODEL", "PROOFMERGE_ALLOW_CONTRIBUTOR"):
        monkeypatch.delenv(name, raising=False)


def test_role_models_can_be_overridden(monkeypatch):
    monkeypatch.setenv("PROOFMERGE_SPARK_MODEL", "glm-5.3")
    monkeypatch.setenv("PROOFMERGE_WONDER_MODEL", "kimi-k3")
    fake = FakeCompletions()
    client = MetaClient(fake)
    ask(client)
    ask(client, WONDER_MODEL)
    assert [r["model"] for r in fake.requests] == ["glm-5.3", "kimi-k3"]


def test_contributor_override_needs_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("PROOFMERGE_SPARK_MODEL", "muse-spark-1.3-contributor")
    fake = FakeCompletions()
    with pytest.raises(MetaError, match="PROOFMERGE_ALLOW_CONTRIBUTOR"):
        ask(MetaClient(fake))
    assert fake.requests == []

    monkeypatch.setenv("PROOFMERGE_ALLOW_CONTRIBUTOR", "1")
    client = MetaClient(fake)
    ask(client)
    assert client.allow_contributor and fake.requests[0]["model"] == "muse-spark-1.3-contributor"


def test_api_endpoint_is_recorded_without_credentials(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setenv("PROOFMERGE_BASE_URL", "https://user:pw@example.test/go/v1?token=t#x")
    client = MetaClient()
    assert client.endpoint == "https://example.test/go/v1"
    assert client.models == {SPARK_MODEL: SPARK_MODEL, WONDER_MODEL: WONDER_MODEL}

    monkeypatch.setenv("PROOFMERGE_BASE_URL", "http://localhost:11434/v1")
    assert MetaClient().endpoint == "http://localhost:11434/v1"


def test_unknown_backend_is_refused(monkeypatch):
    monkeypatch.setenv("PROOFMERGE_BACKEND", "grok-cli")
    with pytest.raises(MetaError, match="unknown PROOFMERGE_BACKEND 'grok-cli'"):
        MetaClient()


def test_cli_backend_uses_the_cli_default_model(monkeypatch):
    monkeypatch.setenv("PROOFMERGE_BACKEND", "claude-cli")
    client = MetaClient()
    assert client.endpoint == "cli:claude-cli"
    assert client.models == {SPARK_MODEL: "", WONDER_MODEL: ""}


class FakeRunner:
    def __init__(self, stdout="", returncode=0, error=None):
        self.stdout, self.returncode, self.error = stdout, returncode, error
        self.calls = []

    def __call__(self, argv, **kwargs):
        workdir = Path(kwargs["cwd"])
        self.calls.append({"argv": argv, "files": sorted(p.name for p in workdir.iterdir()),
                           "system": (workdir / "system.txt").read_text(), **kwargs})
        if self.error:
            raise self.error
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr="SECRET")


def claude_out(**fields):
    out = {"type": "result", "is_error": False, "structured_output": {"ok": True, "note": "hi"},
           "usage": {"input_tokens": 2, "cache_creation_input_tokens": 10,
                     "cache_read_input_tokens": 5, "output_tokens": 7}}
    return json.dumps(out | fields)


def test_claude_cli_runs_without_tools_in_an_empty_dir(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "must-not-reach-the-cli")
    runner = FakeRunner(claude_out())
    client = MetaClient(CliCompletions("claude-cli", runner))
    history = [{"role": "assistant", "content": "draft"}, {"role": "user", "content": "fix it"}]

    reply = client.structured(SPARK_MODEL, developer="rules", user="question", schema=Answer,
                              history=history)

    assert reply == Answer(ok=True, note="hi")
    call = runner.calls[0]
    argv = call["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == Answer.model_json_schema()
    assert argv[-2:] == ["--model", SPARK_MODEL]
    assert call["files"] == ["system.txt"] and call["system"] == "rules"
    assert "MODEL_API_KEY" not in call["env"]
    assert call["input"].index("question") < call["input"].index("draft") < call["input"].index(
        "fix it")
    assert (client.usage[0].prompt_tokens, client.usage[0].completion_tokens) == (17, 7)


def test_claude_cli_omits_model_for_the_cli_default():
    runner = FakeRunner(claude_out())
    CliCompletions("claude-cli", runner).create(
        model="", messages=[{"role": "developer", "content": "d"}, {"role": "user", "content": "u"}],
        response_format={"json_schema": {"schema": {}}},
    )
    assert "--model" not in runner.calls[0]["argv"]


@pytest.mark.parametrize(("runner", "message"), [
    (FakeRunner(returncode=1), "exited with code 1"),
    (FakeRunner("not json"), "did not print JSON"),
    (FakeRunner(claude_out(is_error=True)), "reported an error"),
    (FakeRunner(error=FileNotFoundError()), "claude is not installed"),
    (FakeRunner(error=subprocess.TimeoutExpired("claude", 1)), "timed out"),
])
def test_cli_failures_are_model_errors_without_stderr(runner, message):
    with pytest.raises(MetaError, match=message) as error:
        ask(MetaClient(CliCompletions("claude-cli", runner)))
    assert "SECRET" not in str(error.value)


def test_report_names_models_and_warns_on_contributor():
    proof = Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())
    assert "via <code>" not in render_html(proof, FIXTURES)  # older proofs record no backend

    proof.model_usage = ModelUsage(
        calls=1, prompt_tokens=1, completion_tokens=1, endpoint="cli:claude-cli",
        models={"spark": "", "wonder": "<b>x</b>"}, contributor_allowed=True,
    )
    page = render_html(proof, FIXTURES)
    assert "spark CLI default, wonder &lt;b&gt;x&lt;/b&gt; via <code>cli:claude-cli</code>" in page
    assert "may be used for training" in page
