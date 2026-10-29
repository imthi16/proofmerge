from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proofmerge import cli
from proofmerge.sandbox import SandboxError, image_digest, runner_dockerfile_path

runner = CliRunner()


class _FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeFiles:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __truediv__(self, other: str) -> _FakeFiles:
        return _FakeFiles(self._path / other)

    def is_file(self) -> bool:
        return self._path.is_file()

    def __fspath__(self) -> str:
        return str(self._path)


def test_runner_dockerfile_path_from_source_checkout() -> None:
    path = runner_dockerfile_path()
    assert path.name == "Dockerfile"
    assert path.exists()


def test_runner_dockerfile_path_packaged(tmp_path: Path, monkeypatch) -> None:
    packaged = tmp_path / "runner" / "Dockerfile"
    packaged.parent.mkdir()
    packaged.write_text("FROM python:3.13-slim\n")
    # resources.files("proofmerge") returns the package root.
    monkeypatch.setattr(
        "proofmerge.sandbox.resources.files", lambda package: _FakeFiles(tmp_path)
    )
    assert runner_dockerfile_path() == packaged


def test_setup_command_runs_docker_argv_list(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1] == "build":
            return _FakeResult(0, "build ok\n", "")
        return _FakeResult(0, "sha256:abc123\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 0
    assert "sha256:abc123" in result.output
    expected_context = str(runner_dockerfile_path().parent)
    assert calls == [
        ["docker", "build", "-t", "proofmerge-runner:0.1", expected_context],
        ["docker", "image", "inspect", "--format", "{{.Id}}", "proofmerge-runner:0.1"],
    ]


def test_setup_command_docker_missing(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 1
    assert "docker" in result.output.lower()


def test_setup_command_build_failure(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        return _FakeResult(1, "", "unknown flag")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 1
    assert "unknown flag" in result.output


def test_image_digest_message_mentions_setup(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        return _FakeResult(1, "", "No such image")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SandboxError, match="proofmerge setup"):
        image_digest()
