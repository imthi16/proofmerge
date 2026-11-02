"""Choosing the model from the app: Meta Muse, a Claude plan through Claude Code, or another API."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.dashboard_jobs import JobError, Jobs

ENV = ("MODEL_API_KEY", "PROOFMERGE_BACKEND", "PROOFMERGE_BASE_URL",
       "PROOFMERGE_SPARK_MODEL", "PROOFMERGE_WONDER_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def runner(claude: bool):
    def run(argv, **_):
        if argv[0] == "claude" and not claude:
            raise FileNotFoundError("claude")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    return run


def test_meta_is_the_default_and_needs_a_key(tmp_path: Path) -> None:
    jobs = Jobs(tmp_path, runner(claude=False))
    assert jobs.provider() == "meta" and jobs.health()["model_key"] is False
    jobs.set_key("sk-test-12345678")
    assert jobs.health()["model_key"] is True
    assert "PROOFMERGE_BACKEND" not in jobs.environment()  # nothing chosen: the environment decides


def test_claude_plan_needs_no_key_only_the_claude_cli(tmp_path: Path) -> None:
    jobs = Jobs(tmp_path, runner(claude=True))
    jobs.set_model({"provider": "claude"})
    health = jobs.health()
    assert health["provider"] == "claude" and health["model_key"] is True and health["claude_cli"] is True
    env = jobs.environment()
    assert env["PROOFMERGE_BACKEND"] == "claude-cli" and env["PROOFMERGE_SPARK_MODEL"] == ""
    assert Jobs(tmp_path, runner(claude=False), settings_file=None).health()["model_key"] is False

    missing = Jobs(tmp_path, runner(claude=False))
    missing.set_model({"provider": "claude", "spark_model": "sonnet", "wonder_model": "haiku"})
    assert missing.health()["model_key"] is False
    assert missing.environment()["PROOFMERGE_WONDER_MODEL"] == "haiku"


def test_another_api_sets_endpoint_and_models_and_uses_the_key(tmp_path: Path) -> None:
    jobs = Jobs(tmp_path, runner(claude=False))
    jobs.set_model({"provider": "openai", "base_url": "https://api.example.com/v1",
                    "spark_model": "model-a", "wonder_model": "model-b"})
    jobs.set_key("sk-test-12345678")
    env = jobs.environment()
    assert env["PROOFMERGE_BACKEND"] == "api" and env["PROOFMERGE_BASE_URL"] == "https://api.example.com/v1"
    assert (env["PROOFMERGE_SPARK_MODEL"], env["PROOFMERGE_WONDER_MODEL"]) == ("model-a", "model-b")
    assert env["MODEL_API_KEY"] == "sk-test-12345678" and jobs.health()["model_key"] is True
    jobs.set_model({"provider": "openai", "base_url": "http://localhost:11434/v1",
                    "spark_model": "llama3.2:3b", "wonder_model": "qwen2.5:7b"})  # a model on this computer


@pytest.mark.parametrize("bad", [
    {"provider": "codex"},
    {"provider": "openai", "base_url": "http://api.example.com/v1", "spark_model": "a", "wonder_model": "b"},
    {"provider": "openai", "base_url": "https://user:pw@api.example.com/v1", "spark_model": "a", "wonder_model": "b"},
    {"provider": "openai", "base_url": "file:///etc/passwd", "spark_model": "a", "wonder_model": "b"},
    {"provider": "openai", "base_url": "https://api.example.com/v1", "spark_model": "a"},
    {"provider": "claude", "spark_model": "sonnet; rm -rf /"},
    "meta",
])
def test_bad_choices_are_refused_and_change_nothing(tmp_path: Path, bad) -> None:
    jobs = Jobs(tmp_path, runner(claude=False))
    jobs.set_model({"provider": "claude"})
    with pytest.raises(JobError):
        jobs.set_model(bad)
    assert jobs.model["provider"] == "claude"


def test_meta_ignores_model_overrides_and_clears_the_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PROOFMERGE_BASE_URL", "https://elsewhere.example/v1")
    jobs = Jobs(tmp_path, runner(claude=False))
    assert jobs.provider() == "openai"  # from the environment until the page chooses
    jobs.set_model({"provider": "meta", "spark_model": "other"})
    env = jobs.environment()
    assert env["PROOFMERGE_BASE_URL"] == "" and env["PROOFMERGE_SPARK_MODEL"] == ""


def test_the_choice_is_saved_without_the_key_and_read_back(tmp_path: Path) -> None:
    settings = tmp_path / "ProofMerge" / "settings.json"
    jobs = Jobs(tmp_path, runner(claude=True), settings_file=settings)
    jobs.set_key("sk-secret-12345678")
    jobs.set_model({"provider": "claude", "wonder_model": "haiku"})
    saved = settings.read_text()
    assert "sk-secret" not in saved and json.loads(saved)["model"]["provider"] == "claude"

    again = Jobs(tmp_path, runner(claude=True), settings_file=settings)
    assert again.provider() == "claude" and again.model["wonder_model"] == "haiku"

    settings.write_text('{"model": {"provider": "openai", "base_url": "ftp://x"}}')  # hand-edited
    assert Jobs(tmp_path, runner(claude=True), settings_file=settings).provider() == "meta"
