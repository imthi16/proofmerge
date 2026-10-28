import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from proofmerge import sandbox
from proofmerge.sandbox import SandboxError, export_tree, outcome_for, run_experiment

ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    (path / "calc.py").write_text("def double(x):\n    return x * 2\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "base")
    base = git(path, "rev-parse", "HEAD")
    (path / "calc.py").write_text("def double(x):\n    return x + 2\n")
    git(path, "commit", "-qam", "head")
    return path, base, git(path, "rev-parse", "HEAD")


def test_export_tree_writes_that_commit_without_git(repo, tmp_path):
    path, base, head = repo
    export_tree(path, base, tmp_path / "base")
    export_tree(path, head, tmp_path / "head")
    assert "x * 2" in (tmp_path / "base" / "calc.py").read_text()
    assert "x + 2" in (tmp_path / "head" / "calc.py").read_text()
    assert not (tmp_path / "base" / ".git").exists()


@pytest.mark.parametrize("bad", ["HEAD", "main", "--output=/tmp/x", "abc123"])
def test_export_tree_refuses_anything_but_a_full_sha(repo, tmp_path, bad):
    with pytest.raises(SandboxError, match="full commit SHA"):
        export_tree(repo[0], bad, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (0, "PASS"),
        (1, "FAIL"),
        (2, "ERROR"),
        (5, "ERROR"),
        (125, "ERROR"),
        (137, "ERROR"),
        (None, "TIMEOUT"),
    ],
)
def test_outcome_for_counts_only_pytest_exit_1_as_failure(code, outcome):
    assert outcome_for(code) == outcome


def test_run_experiment_records_every_attempt_on_both_revisions(repo, tmp_path):
    path, base, head = repo
    seen: list[str] = []

    def fake_runner(code_dir, probe_dir, probe_name, timeout):
        seen.append((code_dir / "calc.py").read_text())
        assert (probe_dir / probe_name).read_text() == "PROBE"
        return (0, b"ok", b"") if "x * 2" in seen[-1] else (1, b"assert 4 == 6", b"")

    evidence = tmp_path / "evidence"
    experiment = run_experiment(
        path, base, head, "H1", "PROBE", evidence, runner=fake_runner, digest="sha256:img"
    )
    outcomes = [(e.revision, e.attempt, e.outcome) for e in experiment.executions]
    assert outcomes == [
        ("base", 1, "PASS"), ("base", 2, "PASS"), ("base", 3, "PASS"),
        ("head", 1, "FAIL"), ("head", 2, "FAIL"), ("head", 3, "FAIL"),
    ]  # fmt: skip
    assert experiment.test_source_sha256 == hashlib.sha256(b"PROBE").hexdigest()
    assert (evidence / "probes" / "test_h1.py").read_text() == "PROBE"
    assert (evidence / experiment.executions[3].stdout_path).read_bytes() == b"assert 4 == 6"
    assert {e.runner_image_digest for e in experiment.executions} == {"sha256:img"}
    assert {e.commit_sha for e in experiment.executions} == {base, head}


def test_run_experiment_rejects_an_id_that_would_escape_the_evidence_dir(repo, tmp_path):
    path, base, head = repo
    with pytest.raises(SandboxError, match="hypothesis id"):
        run_experiment(path, base, head, "../H1", "PROBE", tmp_path, runner=None, digest="x")


def docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        sandbox.image_digest()
    except SandboxError:
        return False
    return True


needs_docker = pytest.mark.skipif(not docker_ready(), reason="docker or runner image missing")


@pytest.fixture(scope="module")
def demo_repo(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("demo") / "demo-shop"
    subprocess.run(
        [sys.executable, str(ROOT / "demo" / "build_demo_repo.py"), str(target)],
        check=True,
        capture_output=True,
    )
    return target


@needs_docker
def test_docker_ground_truth_probe_passes_on_base_and_fails_on_head(demo_repo, tmp_path):
    base = git(demo_repo, "rev-parse", "main")
    head = git(demo_repo, "rev-parse", "candidate/tenant-cache-regression")
    probe = (ROOT / "demo/ground_truth/tenant-cache-regression/test_cross_tenant.py").read_text()
    experiment = run_experiment(demo_repo, base, head, "H1", probe, tmp_path)
    outcomes = {e.revision: [x.outcome for x in experiment.executions if x.revision == e.revision]
                for e in experiment.executions}  # fmt: skip
    assert outcomes == {"base": ["PASS"] * 3, "head": ["FAIL"] * 3}


ISOLATION_PROBE = """
import os, socket, pathlib, pytest

def test_no_host_secrets():
    assert "MODEL_API_KEY" not in os.environ

def test_no_network():
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 53), timeout=2)

def test_code_is_read_only():
    with pytest.raises(OSError):
        pathlib.Path("/work/code/new_file").write_text("x")

def test_not_root():
    assert os.getuid() != 0
"""


@needs_docker
def test_docker_container_has_no_network_secrets_or_write_access(demo_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "must-not-leak")
    base = git(demo_repo, "rev-parse", "main")
    experiment = run_experiment(demo_repo, base, base, "H9", ISOLATION_PROBE, tmp_path, attempts=1)
    for execution in experiment.executions:
        log = (tmp_path / execution.stdout_path).read_text()
        assert execution.outcome == "PASS", log
        assert "must-not-leak" not in log
