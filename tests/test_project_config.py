"""Offline tests for `.proofmerge.toml` parsing and validation."""

import subprocess
from pathlib import Path

import pytest

from proofmerge.project_config import (
    ProjectConfig,
    PythonProject,
    assert_operator_config_allowed,
    load_config,
    load_operator_config,
    load_python_project,
    load_setup,
)

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


PYTHON_BASE = """
[python]
root = "apps/api"
pythonpath = ["libs/shared"]
"""

PYTHON_HEAD = """
[python]
root = "apps/evil"
"""


def test_python_root_rejects_absolute_traversal_and_empty_extra():
    with pytest.raises(ValueError, match="absolute"):
        PythonProject(root="/etc")
    with pytest.raises(ValueError, match="absolute"):
        PythonProject(pythonpath=["/tmp/lib"])
    with pytest.raises(ValueError, match=r"\.\."):
        PythonProject(root="apps/../../etc")
    with pytest.raises(ValueError, match="empty"):
        PythonProject(pythonpath=["."])
    with pytest.raises(ValueError, match="network"):
        PythonProject(root="apps/api", network="bridge")


def test_python_only_file_keeps_generic_config_unset(tmp_path):
    repo = make_repo(tmp_path, PYTHON_BASE, PYTHON_HEAD)
    base = git(repo, "rev-parse", "HEAD~1")
    head = git(repo, "rev-parse", "HEAD")
    assert load_config(repo, base) is None
    loaded = load_python_project(repo, base)
    assert loaded is not None
    assert loaded.root == "apps/api"
    assert loaded.pythonpath == ["libs/shared"]
    assert loaded.install is False
    assert load_python_project(repo, head).root == "apps/evil"
    project, python = load_setup(repo, base)
    assert project is None and python.root == "apps/api"


def test_head_python_escape_does_not_affect_base(tmp_path):
    repo = make_repo(tmp_path, PYTHON_BASE, '[python]\nroot = "../secret"\n')
    base = git(repo, "rev-parse", "HEAD~1")
    head = git(repo, "rev-parse", "HEAD")
    assert load_python_project(repo, base).root == "apps/api"
    with pytest.raises(ValueError, match="invalid"):
        load_setup(repo, head)


def test_language_and_python_together_are_rejected(tmp_path):
    repo = make_repo(tmp_path, VALID_TOML + '\n[python]\nroot = "apps/api"\n')
    base = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match=r"\[python\]"):
        load_config(repo, base)


def test_operator_file_outside_the_checkout_is_trusted(tmp_path):
    trusted = tmp_path / "trusted.toml"
    trusted.write_text(PYTHON_BASE, encoding="utf-8")
    project, python = load_operator_config(trusted)
    assert project is None
    assert python.root == "apps/api"


def test_operator_config_refuses_a_symlink(tmp_path):
    target = tmp_path / "real.toml"
    target.write_text(PYTHON_BASE, encoding="utf-8")
    link = tmp_path / "link.toml"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        load_operator_config(link)


def test_operator_config_refuses_working_tree_file_that_differs_from_base(tmp_path):
    repo = make_repo(tmp_path, PYTHON_BASE, PYTHON_HEAD)
    base = git(repo, "rev-parse", "HEAD~1")
    with pytest.raises(ValueError, match="refusing"):
        assert_operator_config_allowed(repo, base, repo / ".proofmerge.toml")
    outside = tmp_path / "trusted.toml"
    outside.write_text(PYTHON_BASE, encoding="utf-8")
    assert_operator_config_allowed(repo, base, outside)
