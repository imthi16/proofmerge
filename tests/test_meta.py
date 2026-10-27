from types import SimpleNamespace

import httpx2
import openai
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from proofmerge.cli import app
from proofmerge.meta import SPARK_MODEL, MetaClient, MetaError


class Answer(BaseModel):
    ok: bool
    note: str


class FakeCompletions:
    """Stands in for openai's chat.completions; records every request."""

    def __init__(self, content: str = '{"ok": true, "note": "hi"}', error: Exception | None = None):
        self.content = content
        self.error = error
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def ask(client: MetaClient, model: str = SPARK_MODEL) -> Answer:
    return client.structured(model, developer="rules", user="question", schema=Answer)


def test_structured_returns_validated_model_and_sends_documented_params():
    fake = FakeCompletions()
    assert ask(MetaClient(fake)) == Answer(ok=True, note="hi")

    request = fake.requests[0]
    assert request["model"] == SPARK_MODEL
    assert request["messages"] == [
        {"role": "developer", "content": "rules"},
        {"role": "user", "content": "question"},
    ]
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Answer", "schema": Answer.model_json_schema()},
    }
    assert request["reasoning_effort"] == "low"
    assert "max_completion_tokens" in request
    # Spark is tuned for default sampling; these must never be sent.
    for forbidden in ("temperature", "top_p", "logprobs", "max_tokens"):
        assert forbidden not in request


def test_schema_invalid_reply_raises():
    with pytest.raises(MetaError, match="did not match Answer"):
        ask(MetaClient(FakeCompletions(content='{"ok": "maybe"}')))


def test_non_json_reply_raises():
    with pytest.raises(MetaError, match="did not match Answer"):
        ask(MetaClient(FakeCompletions(content="not json")))


def test_call_cap_blocks_extra_calls():
    fake = FakeCompletions()
    client = MetaClient(fake, max_calls=1)
    ask(client)
    with pytest.raises(MetaError, match="budget"):
        ask(client)
    assert len(fake.requests) == 1


def test_contributor_models_are_rejected_before_any_request():
    fake = FakeCompletions()
    with pytest.raises(MetaError, match="contributor"):
        ask(MetaClient(fake), model="muse-spark-1.3-contributor")
    assert fake.requests == []


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(MetaError, match="MODEL_API_KEY"):
        MetaClient()


def test_auth_error_is_clear_and_never_leaks_the_key(monkeypatch):
    secret = "sk-test-SECRET-123"
    monkeypatch.setenv("MODEL_API_KEY", secret)
    request = httpx2.Request("POST", "https://api.meta.ai/v1/chat/completions")
    error = openai.AuthenticationError(
        f"Incorrect API key provided: {secret}",
        response=httpx2.Response(401, request=request),
        body=None,
    )
    with pytest.raises(MetaError) as caught:
        ask(MetaClient(FakeCompletions(error=error)))
    assert "401" in str(caught.value)
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


def test_smoke_without_key_exits_cleanly():
    result = CliRunner().invoke(app, ["smoke"], env={"MODEL_API_KEY": ""})
    assert result.exit_code == 1
    assert "MODEL_API_KEY" in result.output
