"""Offline tests for `.proofmerge.toml` parsing and validation."""

import subprocess
from pathlib import Path

import pytest

from proofmerge.project_config import ProjectConfig, load_config

VALID_TOML = """
language = "go"
image = "golang:1.23-alpine"
probe_path = "pricing/proofmerge_probe_test.go"
test_command = ["go", "test", "./pricing"]
assertion_failure = "FAIL: TestProofMergeProbe"
pass_exit_codes = [0]
tmpfs_mb = 512

[env]
GOCACHE = "/tmp/gocache"
HOME = "/tmp"
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(tmp_path: Path, base_toml: str | None, head_toml: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    if base_toml is not None:
        (repo / ".proofmerge.toml").write_text(base_toml, encoding="utf-8")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    if head_toml is not None:
        (repo / ".proofmerge.toml").write_text(head_toml, encoding="utf-8")
        (repo / "file.txt").write_text("head\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "head")
    return repo


def test_valid_config_parses():
    config = ProjectConfig.model_validate(
        {
            "language": "go",
            "image": "golang:1.23-alpine",
            "probe_path": "pricing/proofmerge_probe_test.go",
            "test_command": ["go", "test", "./pricing"],
            "assertion_failure": "FAIL: TestProofMergeProbe",
        }
    )
    assert config.language == "go"
    assert config.tmpfs_mb == 256
    assert config.pass_exit_codes == [0]
    assert config.env == {}


def test_language_rejects_bad_characters():
    with pytest.raises(ValueError, match="language"):
        ProjectConfig(language="Go!")


def test_language_rejects_long_value():
    with pytest.raises(ValueError, match="language"):
        ProjectConfig(language="a" * 21)


def test_probe_path_rejects_absolute():
    with pytest.raises(ValueError, match="probe_path"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="/etc/passwd",
            test_command=["t"],
            assertion_failure="fail",
        )


def test_probe_path_rejects_dotdot():
    with pytest.raises(ValueError, match="probe_path"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="../secret.go",
            test_command=["t"],
            assertion_failure="fail",
        )


def test_probe_path_rejects_bad_characters():
    with pytest.raises(ValueError, match="probe_path"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="probe@.go",
            test_command=["t"],
            assertion_failure="fail",
        )


def test_test_command_must_be_non_empty():
    with pytest.raises(ValueError, match="test_command"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="p.go",
            test_command=[],
            assertion_failure="fail",
        )


def test_assertion_failure_must_be_valid_regex():
    with pytest.raises(ValueError, match="assertion_failure"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="p.go",
            test_command=["t"],
            assertion_failure="[unclosed",
        )


def test_tmpfs_mb_out_of_range():
    with pytest.raises(ValueError, match="tmpfs_mb"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="p.go",
            test_command=["t"],
            assertion_failure="fail",
            tmpfs_mb=8,
        )


def test_env_keys_must_be_uppercase():
    with pytest.raises(ValueError, match="env"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="p.go",
            test_command=["t"],
            assertion_failure="fail",
            env={"lower": "1"},
        )


def test_extra_fields_are_forbidden():
    with pytest.raises(ValueError, match="network"):
        ProjectConfig(
            language="go",
            image="i",
            probe_path="p.go",
            test_command=["t"],
            assertion_failure="fail",
            network="bridge",
        )


def test_load_config_reads_from_base(tmp_path):
    repo = make_repo(tmp_path, VALID_TOML)
    base = git(repo, "rev-parse", "HEAD")
    config = load_config(repo, base)
    assert config is not None
    assert config.language == "go"
    assert config.image == "golang:1.23-alpine"


def test_load_config_returns_none_when_missing(tmp_path):
    repo = make_repo(tmp_path, None)
    base = git(repo, "rev-parse", "HEAD")
    assert load_config(repo, base) is None


def test_load_config_reads_base_version_not_head(tmp_path):
    head_toml = VALID_TOML.replace("go", "rust")
    repo = make_repo(tmp_path, VALID_TOML, head_toml)
    base = git(repo, "rev-parse", "HEAD~1")
    head = git(repo, "rev-parse", "HEAD")
    assert load_config(repo, base).language == "go"
    assert load_config(repo, head).language == "rust"


def test_load_config_raises_on_invalid_toml(tmp_path):
    repo = make_repo(tmp_path, "this is not toml")
    base = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="TOML"):
        load_config(repo, base)


def test_load_config_raises_on_invalid_config(tmp_path):
    repo = make_repo(tmp_path, 'language = "go"')
    base = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="invalid"):
        load_config(repo, base)
