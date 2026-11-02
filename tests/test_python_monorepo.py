"""Monorepo import paths and a declared dependency, exercised by probes this file supplies.

These runs test the runner. They are not a model verdict and not evidence that
ProofMerge detected a regression on its own.
"""

import hashlib
import io
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

from proofmerge import pipeline
from proofmerge.cli import app
from proofmerge.findings import classify
from proofmerge.hypotheses import HypothesisSet
from proofmerge.intent import C0, IntentContract
from proofmerge.meta import MetaClient
from proofmerge.pipeline import run_verify
from proofmerge.project_config import load_python_project
from proofmerge.python_layout import install_hashed_wheels
from proofmerge.sandbox import host_python_runner, run_experiment

BEHAVIOR = """
from app.text import format_context

def test_passage_case():
    assert format_context([{"content": "Returns are accepted."}]) == "[1] Returns are accepted."
"""

DEPENDENCY = """
from app.wrapped import greet

def test_greet():
    assert greet("Ada") == "hello Ada"
"""

MISSING = """
import app.missing as missing

def test_missing():
    assert missing.value() == 1
"""

SECRET = """
import os

def test_no_secret():
    assert "MODEL_API_KEY" not in os.environ
    assert "secret-value" not in os.environ.get("PYTHONPATH", "")
"""


def git(repo: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _wheel(tmp: Path) -> tuple[Path, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            "greetlib/__init__.py",
            "def hello(name):\n    return f'hello {name}'\n",
        )
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    path = tmp / "wheelhouse" / "greetlib-1.0.0-py3-none-any.whl"
    path.parent.mkdir()
    path.write_bytes(data)
    return path.parent, digest


def _lock(digest: str, version: str = "1.0.0") -> str:
    return f"""
version = 1
[[package]]
name = "demo-api"
version = "0.1.0"
source = {{ virtual = "." }}
dependencies = [{{ name = "greetlib" }}]

[[package]]
name = "greetlib"
version = "{version}"
source = {{ registry = "https://pypi.org/simple" }}
wheels = [{{ url = "https://example.invalid/greetlib.whl", hash = "sha256:{digest}" }}]
"""


def _text(lower: bool) -> str:
    value = "chunk['content'].lower()" if lower else "chunk['content']"
    return (
        "def format_context(chunks):\n"
        "    lines = []\n"
        "    for i, chunk in enumerate(chunks, start=1):\n"
        f"        lines.append('[' + str(i) + '] ' + {value})\n"
        "    return '\\n'.join(lines)\n"
    )


def monorepo(tmp_path: Path) -> tuple[Path, str, str, Path]:
    wheelhouse, digest = _wheel(tmp_path)
    repo = tmp_path / "repo"
    api = repo / "apps" / "api" / "app"
    api.mkdir(parents=True)
    (api / "__init__.py").write_text("", encoding="utf-8")
    (api / "text.py").write_text(_text(False), encoding="utf-8")
    (api / "wrapped.py").write_text(
        "import greetlib\n\ndef greet(name):\n    return greetlib.hello(name)\n",
        encoding="utf-8",
    )
    (api / "missing.py").write_text(
        "import package_that_is_not_installed_anywhere\n\n"
        "def value():\n    return package_that_is_not_installed_anywhere.value\n",
        encoding="utf-8",
    )
    (repo / "apps" / "api" / "pyproject.toml").write_text(
        '[project]\nname = "demo-api"\nversion = "0.1.0"\ndependencies = ["greetlib==1.0.0"]\n',
        encoding="utf-8",
    )
    (repo / "apps" / "api" / "uv.lock").write_text(_lock(digest), encoding="utf-8")
    (repo / ".proofmerge.toml").write_text(
        '[python]\nroot = "apps/api"\ninstall = true\npyproject = "apps/api/pyproject.toml"\n'
        'lockfile = "apps/api/uv.lock"\n',
        encoding="utf-8",
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (api / "text.py").write_text(_text(True), encoding="utf-8")
    (repo / "apps" / "api" / "uv.lock").write_text(_lock("0" * 64, version="9.9.9"), encoding="utf-8")
    (repo / ".proofmerge.toml").write_text('[python]\nroot = "apps/evil"\ninstall = true\n', encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    return repo, base, git(repo, "rev-parse", "HEAD"), wheelhouse


def _install(wheelhouse: Path):
    def installer(requirements: str, dest: Path, index_url: str) -> None:
        assert index_url == "https://pypi.org/simple"
        assert "greetlib==1.0.0" in requirements
        assert "9.9.9" not in requirements
        install_hashed_wheels(requirements, wheelhouse, dest)

    return installer


def _run(repo, base, head, wheelhouse, probe, hypothesis_id, evidence):
    project = load_python_project(repo, base)
    assert project is not None and project.root == "apps/api"
    assert load_python_project(repo, head).root == "apps/evil"
    return run_experiment(
        repo, base, head, hypothesis_id, probe, evidence,
        runner=host_python_runner, digest="sha256:local", python_project=project,
        installer=_install(wheelhouse),
    )


def test_supplied_probe_imports_subdir_package_and_sees_the_behavior_change(tmp_path):
    repo, base, head, wheelhouse = monorepo(tmp_path)
    experiment = _run(repo, base, head, wheelhouse, BEHAVIOR, "H1", tmp_path / "behavior")
    outcomes = [(item.revision, item.outcome) for item in experiment.executions]
    assert outcomes == [("base", "PASS")] * 3 + [("head", "FAIL")] * 3
    assert classify(experiment) == "REGRESSION"
    assert all(not item.setup_failure for item in experiment.executions)
    requirements = (tmp_path / "behavior" / "python-requirements.txt").read_text()
    assert "greetlib==1.0.0" in requirements
    assert "9.9.9" not in requirements


def test_supplied_probe_imports_a_declared_dependency_on_both_revisions(tmp_path):
    repo, base, head, wheelhouse = monorepo(tmp_path)
    experiment = _run(repo, base, head, wheelhouse, DEPENDENCY, "H2", tmp_path / "dep")
    assert [item.outcome for item in experiment.executions] == ["PASS"] * 6
    assert classify(experiment) == "NO_DIFFERENCE"


def test_missing_import_is_environment_on_both_revisions(tmp_path):
    repo, base, head, wheelhouse = monorepo(tmp_path)
    experiment = _run(repo, base, head, wheelhouse, MISSING, "H3", tmp_path / "missing")
    assert [item.outcome for item in experiment.executions] == ["ERROR"] * 6
    assert all(item.setup_failure for item in experiment.executions)
    assert classify(experiment) == "ENVIRONMENT"


def test_without_python_root_the_subdir_package_does_not_import(tmp_path):
    repo, base, head, _wheelhouse = monorepo(tmp_path)
    experiment = run_experiment(
        repo, base, head, "H4", BEHAVIOR, tmp_path / "default",
        runner=host_python_runner, digest="sha256:local", attempts=1,
    )
    assert [item.outcome for item in experiment.executions] == ["ERROR", "ERROR"]
    assert all(item.setup_failure for item in experiment.executions)


def test_default_layout_still_imports_src_and_repo_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "secret-value")
    repo = tmp_path / "flat"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "calc.py").write_text("def double(x):\n    return x * 2\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text("def double(x):\n    return x + 2\n", encoding="utf-8")
    git(repo, "commit", "-qam", "head")
    head = git(repo, "rev-parse", "HEAD")
    probe = (
        "from pkg.mod import VALUE\n"
        "import calc\n"
        "def test_layout():\n"
        "    assert VALUE == 1\n"
        "    assert calc.double(3) == 6\n"
    )
    experiment = run_experiment(
        repo, base, head, "H5", probe, tmp_path / "flat-ev",
        runner=host_python_runner, digest="sha256:local", attempts=1,
    )
    assert [(item.revision, item.outcome) for item in experiment.executions] == [
        ("base", "PASS"),
        ("head", "FAIL"),
    ]
    secret = run_experiment(
        repo, base, None, "H6", SECRET, tmp_path / "secret",
        runner=host_python_runner, digest="sha256:local", attempts=1,
    )
    assert secret.executions[0].outcome == "PASS"
    assert "secret-value" not in (tmp_path / "secret" / "H6" / "base-1.stdout").read_text()
    assert os.environ["MODEL_API_KEY"] == "secret-value"


def test_python_table_stays_on_the_python_runner(tmp_path, monkeypatch):
    repo, base, head, _wheelhouse = monorepo(tmp_path)
    seen = {}
    monkeypatch.setattr(
        pipeline, "extract_intent", lambda *args, **kwargs: IntentContract(claims=[C0], rejected=[])
    )
    monkeypatch.setattr(
        pipeline, "generate_hypotheses", lambda *args, **kwargs: HypothesisSet(hypotheses=[], rejected=[])
    )
    monkeypatch.setattr(pipeline, "cross_check_claims", lambda *args, **kwargs: frozenset())
    monkeypatch.setattr(
        pipeline, "check_probes", lambda *args, **kwargs: (frozenset(), frozenset(), {})
    )
    monkeypatch.setattr(pipeline, "find_tamper", lambda *args, **kwargs: [])

    def probes(*args, **kwargs):
        seen["config"] = kwargs.get("config")
        seen["python"] = kwargs.get("python_project")
        return [], []

    def tamper(*args, **kwargs):
        seen["tamper"] = kwargs.get("python_project")
        return [], []

    monkeypatch.setattr(pipeline, "probe_hypotheses", probes)
    monkeypatch.setattr(pipeline, "run_base_tests_on_head", tamper)
    proof = run_verify(
        repo, base, head, "Keep the passage text.", tmp_path / "ev",
        MetaClient(SimpleNamespace(create=lambda **kwargs: None)),
    )
    assert proof.language == "python"
    assert seen["config"] is None
    assert seen["python"].root == "apps/api"
    assert seen["tamper"].root == "apps/api"
    assert not any("not available" in item for item in proof.verdict.scope_limitations)
    assert any("apps/api" in item for item in proof.verdict.scope_limitations)
    assert any("differs from base" in item for item in proof.verdict.scope_limitations)


def test_verify_config_flag_uses_the_operator_file_not_the_head_checkout(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    repo, base, head, _wheelhouse = monorepo(tmp_path)
    outside = tmp_path / "trusted.toml"
    outside.write_text('[python]\nroot = "apps/api"\n', encoding="utf-8")
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr("proofmerge.cli.run_verify", fake)
    result = CliRunner().invoke(app, [
        "verify", str(repo), "--base", base, "--head", head,
        "--evidence-dir", str(tmp_path / "out"),
        "--config", str(outside),
    ])
    assert captured["python_project"].root == "apps/api"
    assert captured["python_project"].install is False
    assert captured["config_source"].startswith("operator config")
    assert result.exit_code != 0
    refused = CliRunner().invoke(app, [
        "verify", str(repo), "--base", base, "--head", head,
        "--evidence-dir", str(tmp_path / "out2"),
        "--config", str(repo / ".proofmerge.toml"),
    ])
    assert refused.exit_code != 0
    assert "refusing" in refused.output
