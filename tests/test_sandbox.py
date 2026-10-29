import hashlib
import io
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from proofmerge import sandbox
from proofmerge.context import get_context
from proofmerge.sandbox import SandboxError, export_tree, outcome_for, run_experiment
from proofmerge.tamper import find_tamper, run_base_tests_on_head

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


@pytest.mark.parametrize("probe_id", ["H1", "T1"])
def test_run_experiment_records_every_attempt_on_both_revisions(repo, tmp_path, probe_id):
    path, base, head = repo
    seen: list[str] = []

    def fake_runner(image, code_dir, probe_dir, probe_name, timeout):
        assert image == "sha256:img"  # every run uses the recorded image ID, not the tag
        seen.append((code_dir / "calc.py").read_text())
        assert (probe_dir / probe_name).read_text() == "PROBE"
        return (0, b"ok", b"") if "x * 2" in seen[-1] else (1, b"assert 4 == 6", b"")

    evidence = tmp_path / "evidence"
    experiment = run_experiment(
        path, base, head, probe_id, "PROBE", evidence, runner=fake_runner, digest="sha256:img"
    )
    outcomes = [(e.revision, e.attempt, e.outcome) for e in experiment.executions]
    assert outcomes == [
        ("base", 1, "PASS"), ("base", 2, "PASS"), ("base", 3, "PASS"),
        ("head", 1, "FAIL"), ("head", 2, "FAIL"), ("head", 3, "FAIL"),
    ]  # fmt: skip
    assert experiment.test_source_sha256 == hashlib.sha256(b"PROBE").hexdigest()
    assert (evidence / "probes" / f"test_{probe_id.lower()}.py").read_text() == "PROBE"
    assert (evidence / experiment.executions[3].stdout_path).read_bytes() == b"assert 4 == 6"
    assert {e.runner_image_digest for e in experiment.executions} == {"sha256:img"}
    assert {e.commit_sha for e in experiment.executions} == {base, head}


def test_run_experiment_mounts_absolute_paths_for_a_relative_evidence_dir(
    repo, tmp_path, monkeypatch
):
    path, base, head = repo
    monkeypatch.chdir(tmp_path)

    def fake_runner(image, code_dir, probe_dir, probe_name, timeout):
        assert code_dir.is_absolute() and probe_dir.is_absolute()
        return 0, b"", b""

    run_experiment(
        path, base, head, "H1", "P", Path("evidence/run1"), runner=fake_runner, digest="d"
    )
    assert (tmp_path / "evidence/run1/H1/base-1.stdout").exists()


def test_run_experiment_makes_code_and_probe_readable_under_umask_077(repo, tmp_path):
    path, base, head = repo
    modes = []

    def fake_runner(image, code_dir, probe_dir, probe_name, timeout):
        for target in (code_dir, code_dir / "calc.py", probe_dir, probe_dir / probe_name):
            modes.append(target.stat().st_mode & 0o007)
        return 0, b"", b""

    old = os.umask(0o077)
    try:
        run_experiment(path, base, head, "H1", "P", tmp_path / "ev", runner=fake_runner, digest="d")
    finally:
        os.umask(old)
    # "other" (uid 65534 in the container) can read every file and enter every directory
    assert modes and all(mode & 0o004 for mode in modes)
    assert all(mode & 0o001 for mode in modes[0::4] + modes[2::4])


def test_read_capped_keeps_at_most_the_limit_and_flags_overflow(monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_OUTPUT_BYTES", 10)
    chunks: list[bytes] = []
    over = threading.Event()
    sandbox._read_capped(io.BytesIO(b"x" * 25), chunks, over)
    assert b"".join(chunks) == b"x" * 10 and over.is_set()
    chunks, over = [], threading.Event()
    sandbox._read_capped(io.BytesIO(b"x" * 10), chunks, over)
    assert b"".join(chunks) == b"x" * 10 and not over.is_set()


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


NOISY_PROBE = """
import os

def test_floods_output():
    os.write(1, b"x" * 4_000_000)
    assert False  # pytest prints the 4 MB of captured output in the failure report
"""


@needs_docker
def test_docker_run_that_floods_output_is_stopped_and_is_error(demo_repo, tmp_path):
    base = git(demo_repo, "rev-parse", "main")
    experiment = run_experiment(demo_repo, base, base, "H8", NOISY_PROBE, tmp_path, attempts=1)
    for execution in experiment.executions:
        assert execution.outcome == "ERROR"
        assert execution.exit_code == sandbox.OUTPUT_LIMIT_EXIT
        assert (tmp_path / execution.stdout_path).stat().st_size <= sandbox.MAX_OUTPUT_BYTES
        assert b"output over" in (tmp_path / execution.stderr_path).read_bytes()


def test_run_experiment_without_head_runs_only_base(repo, tmp_path):
    path, base, _ = repo
    experiment = run_experiment(
        path, base, None, "H1", "P", tmp_path, runner=lambda *a: (0, b"", b""), digest="d"
    )
    assert [e.revision for e in experiment.executions] == ["base"] * 3
    assert (tmp_path / "probes" / "conftest.py").read_text() == sandbox.OUTCOME_PLUGIN


PLUGIN_CASES = {
    "assert": ("def test_x():\n    assert 1 == 2\n", 1),
    "raises": (("import pytest\n\ndef test_x():\n    with pytest.raises(ValueError):\n"
                "        pass\n"), 1),
    "name_error": ("def test_x():\n    missing_function()\n", sandbox.INVALID_PROBE_EXIT),
    "setup_error": (("import pytest\n\n@pytest.fixture\ndef f():\n    assert False\n\n"
                     "def test_x(f):\n    pass\n"), sandbox.INVALID_PROBE_EXIT),
    "mixed": ("def test_a():\n    assert False\n\ndef test_b():\n    {}['k']\n",
              sandbox.INVALID_PROBE_EXIT),
    "pass": ("def test_x():\n    assert True\n", 0),
    "skip": ("import pytest\n\ndef test_x():\n    pytest.skip('no')\n", sandbox.INVALID_PROBE_EXIT),
    "xfail": (("import pytest\n\n@pytest.mark.xfail\ndef test_x():\n    assert False\n"),
              sandbox.INVALID_PROBE_EXIT),
    "xpass": (("import pytest\n\n@pytest.mark.xfail\ndef test_x():\n    assert True\n"),
              sandbox.INVALID_PROBE_EXIT),
    "pass_and_skip": (("import pytest\n\ndef test_a():\n    assert True\n\n"
                       "@pytest.mark.skip\ndef test_b():\n    assert False\n"),
                      sandbox.INVALID_PROBE_EXIT),
    "module_skip": (("import pytest\n\npytest.importorskip('no_such_module')\n\n"
                     "def test_x():\n    assert True\n"), 5),
}  # fmt: skip


@pytest.mark.parametrize("case", PLUGIN_CASES)
def test_outcome_plugin_counts_only_assertion_failures_as_fail(case, tmp_path):
    source, exit_code = PLUGIN_CASES[case]
    (tmp_path / "test_probe.py").write_text(source)
    (tmp_path / "conftest.py").write_text(sandbox.OUTCOME_PLUGIN)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_probe.py"],
        cwd=tmp_path, capture_output=True, check=False, env={**os.environ, "PYTEST_ADDOPTS": ""},
    )  # fmt: skip
    assert result.returncode == exit_code, result.stdout.decode()


@needs_docker
def test_docker_probe_calling_a_missing_function_is_error_not_fail(demo_repo, tmp_path):
    base = git(demo_repo, "rev-parse", "main")
    probe = "from demo_shop import pricing\n\ndef test_x():\n    pricing.no_such_function()\n"
    experiment = run_experiment(demo_repo, base, None, "H7", probe, tmp_path, attempts=1)
    assert [e.outcome for e in experiment.executions] == ["ERROR"]
    assert experiment.executions[0].exit_code == sandbox.INVALID_PROBE_EXIT


@needs_docker
@pytest.mark.parametrize(
    ("candidate", "expected"),
    [("test-tamper", "FAIL"), ("intended-change", "FAIL"), ("tenant-cache-regression", None)],
)
def test_docker_base_tests_on_head(demo_repo, tmp_path, candidate, expected):
    context = get_context(demo_repo, "main", f"candidate/{candidate}")
    findings = find_tamper(demo_repo, context)
    updated, experiments = run_base_tests_on_head(demo_repo, context, findings, tmp_path)
    if expected is None:
        assert updated == [] and experiments == []
    else:
        assert updated and experiments
        assert all(f.base_test_on_head_outcome == expected for f in updated)
        for experiment in experiments:
            assert [e.outcome for e in experiment.executions] == ["PASS"] * 3 + ["FAIL"] * 3
