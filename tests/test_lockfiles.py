"""poetry.lock and Pipfile.lock become hashed requirements, using only registry wheels.

The fixtures follow the shape Poetry 2.5 and Pipenv 2026.10 actually write (checked
against lockfiles both tools generated for the same requests[socks] project).
"""

import json
import subprocess
from pathlib import Path

import pytest

from proofmerge.lockfiles import plan_pipfile_lock, plan_poetry_lock
from proofmerge.project_config import PythonProject
from proofmerge.python_layout import LayoutError, parse_requirements, requirements_for_base

H = {name: char * 64 for name, char in
     (("attrs", "a"), ("bbb", "b"), ("colorama", "c"), ("requests", "d"), ("socks", "5"),
      ("pytest", "7"))}


def wheel(name: str, digest: str) -> str:
    return f'{{file = "{name}-1.0-py3-none-any.whl", hash = "sha256:{digest}"}}'


def package(name, *, groups='["main"]', extra="", files=None, deps="", extras=""):
    files = files if files is not None else wheel(name, H.get(name, "f" * 64))
    files = f"{files},\n    " if files else ""
    block = f"""
[[package]]
name = "{name}"
version = "1.0"
optional = false
python-versions = ">=3.9"
groups = {groups}
{extra}
files = [
    {files}{{file = "{name}-1.0.tar.gz", hash = "sha256:{'e' * 64}"}},
]
"""
    if deps:
        block += f"\n[package.dependencies]\n{deps}\n"
    if extras:
        block += f"\n[package.extras]\n{extras}\n"
    return block


POETRY_LOCK = (
    package("attrs")
    + package("colorama", groups='["main", "dev"]', extra='markers = "sys_platform == \\"win32\\""')
    + package("pysocks", files=wheel("pysocks", H["socks"]))
    + package("pytest", groups='["dev"]')
    + package(
        "requests",
        deps='PySocks = {version = ">=1.5.6", optional = true, markers = "extra == \\"socks\\""}\n'
             'colorama = {version = "*", markers = "sys_platform == \\"win32\\""}',
        extras='socks = ["PySocks (>=1.5.6,!=1.5.7)"]',
    )
    + '\n[metadata]\nlock-version = "2.1"\npython-versions = ">=3.11"\ncontent-hash = "x"\n'
)
PEP621 = """
[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests[socks]>=2.31", "attrs", "colorama; sys_platform == 'win32'"]

[tool.poetry.group.dev.dependencies]
pytest = "*"
"""


def names(text: str) -> list[str]:
    return [name for name, _, _ in parse_requirements(text)]


def test_poetry_lock_installs_main_wheels_with_requested_extras():
    text, index = plan_poetry_lock(POETRY_LOCK, PEP621, "3.13")
    assert index == "https://pypi.org/simple"
    # pysocks via the socks extra; colorama is Windows-only; pytest is a dev group.
    assert names(text) == ["attrs", "pysocks", "requests"]
    planned = {n: h for n, _, h in parse_requirements(text)}
    assert planned["pysocks"] == [H["socks"]]  # the wheel hash only, never the sdist's


def test_poetry_lock_without_the_extra_skips_its_optional_dependency():
    pyproject = PEP621.replace("requests[socks]>=2.31", "requests>=2.31")
    assert names(plan_poetry_lock(POETRY_LOCK, pyproject, "3.13")[0]) == ["attrs", "requests"]


def test_poetry_one_style_dependency_table():
    pyproject = """
[tool.poetry.dependencies]
python = "^3.11"
requests = {version = "^2.31", extras = ["socks"]}
attrs = "*"
colorama = {version = "*", platform = "win32"}
pytest = {version = "*", optional = true}
"""
    assert names(plan_poetry_lock(POETRY_LOCK, pyproject, "3.13")[0]) == [
        "attrs", "pysocks", "requests",
    ]


@pytest.mark.parametrize(("pyproject", "lock", "message"), [
    ('[project]\ndependencies = ["missing"]\n', POETRY_LOCK, "missing missing"),
    ('[project]\ndependencies = ["attrs @ https://example.invalid/a.whl"]\n', POETRY_LOCK, "URL"),
    ('[tool.poetry.dependencies]\nlocal = {path = "../local"}\n', POETRY_LOCK, "non-registry"),
    ('[project]\ndependencies = ["requests[nope]"]\n', POETRY_LOCK, "no extra 'nope'"),
    ('[project]\ndependencies = ["bbb"]\n', package("bbb", files="") + "", "no wheels"),
    ('[project]\ndependencies = ["bbb"]\n',
     package("bbb") + '\n[package.source]\ntype = "git"\nurl = "https://example.invalid/b"\n',
     "non-registry"),
    ('[project]\ndependencies = ["bbb", "attrs"]\n',
     package("attrs") + package("bbb")
     + '\n[package.source]\ntype = "legacy"\nurl = "https://mirror.example/simple"\n',
     "more than one package index"),
    ('[project]\ndependencies = ["bbb"]\n',
     package("bbb") + '\n[package.source]\ntype = "legacy"\nurl = "http://mirror.example/simple"\n',
     "https"),
])  # fmt: skip
def test_poetry_lock_refusals(pyproject, lock, message):
    with pytest.raises(LayoutError, match=message):
        plan_poetry_lock(lock, pyproject, "3.13")


def test_poetry_lock_uses_one_private_index_and_needs_pyproject():
    lock = package("bbb") + '\n[package.source]\ntype = "legacy"\nurl = "https://mirror.example/simple"\n'
    text, index = plan_poetry_lock(lock, '[project]\ndependencies = ["bbb"]\n', "3.13")
    assert (names(text), index) == (["bbb"], "https://mirror.example/simple")
    with pytest.raises(LayoutError, match="pyproject"):
        plan_poetry_lock(lock, None, "3.13")


def test_poetry_lock_picks_the_version_whose_marker_matches():
    old = package("bbb", extra='markers = "python_version < \\"3.12\\""').replace(
        'version = "1.0"', 'version = "0.9"'
    )
    new = package("bbb", extra='markers = "python_version >= \\"3.12\\""')
    lock = old + new
    pyproject = '[project]\ndependencies = ["bbb"]\n'
    assert parse_requirements(plan_poetry_lock(lock, pyproject, "3.13")[0])[0][1] == "1.0"
    assert parse_requirements(plan_poetry_lock(lock, pyproject, "3.11")[0])[0][1] == "0.9"


def pipfile_lock(default: dict, *, sources=None, develop=None) -> str:
    sources = sources or [{"name": "pypi", "url": "https://pypi.org/simple", "verify_ssl": True}]
    return json.dumps({
        "_meta": {"hash": {"sha256": "x"}, "pipfile-spec": 6,
                  "requires": {"python_version": "3.13"}, "sources": sources},
        "default": default, "develop": develop or {},
    })  # fmt: skip


def entry(name: str, **extra) -> dict:
    return {"hashes": [f"sha256:{H.get(name, 'f' * 64)}", f"sha256:{'e' * 64}"],
            "version": "==1.0", **extra}  # fmt: skip


def test_pipfile_lock_installs_default_packages_for_this_runner():
    lock = pipfile_lock(
        {
            "attrs": entry("attrs", index="pypi"),
            "requests": entry("requests", markers="python_version >= '3.10'", extras=["socks"]),
            # As Pipenv writes it: excluded by its marker, so unpinned and unhashed.
            "colorama": {"markers": "sys_platform == 'win32'", "version": "*"},
            "pysocks": entry("socks", markers="python_version != '3.0'"),
        },
        develop={"pytest": entry("pytest")},
    )
    text, index = plan_pipfile_lock(lock, "3.13")
    assert index == "https://pypi.org/simple"
    assert names(text) == ["attrs", "pysocks", "requests"]
    assert parse_requirements(text)[0][2] == [H["attrs"], "e" * 64]


@pytest.mark.parametrize(("default", "sources", "message"), [
    ({"a": {"version": "*", "hashes": [f"sha256:{'a' * 64}"]}}, None, "not pinned"),
    ({"a": {"version": "==1.0", "hashes": []}}, None, "no sha256"),
    ({"a": {"version": "==1.0", "hashes": [f"md5:{'a' * 32}"]}}, None, "not sha256"),
    ({"a": {"git": "https://example.invalid/a", "ref": "x"}}, None, "non-registry"),
    ({"a": {"path": ".", "editable": True}}, None, "non-registry"),
    ({"a": entry("a", index="other")}, None, "unknown index"),
    ({"a": entry("a")}, [{"name": "pypi", "url": "http://pypi.org/simple"}], "https"),
    ({"a": entry("a", index="pypi"), "b": entry("b", index="mirror")},
     [{"name": "pypi", "url": "https://pypi.org/simple"},
      {"name": "mirror", "url": "https://mirror.example/simple"}], "more than one"),
])  # fmt: skip
def test_pipfile_lock_refusals(default, sources, message):
    with pytest.raises(LayoutError, match=message):
        plan_pipfile_lock(pipfile_lock(default, sources=sources), "3.13")


def test_config_accepts_the_three_lockfiles():
    poetry = PythonProject(install=True, lockfile="apps/api/poetry.lock")
    assert poetry.pyproject == "apps/api/pyproject.toml"
    assert PythonProject(install=True, lockfile="Pipfile.lock").pyproject == ""
    assert PythonProject(install=True, root="svc").lockfile == "svc/uv.lock"
    with pytest.raises(ValueError, match="uv.lock, poetry.lock or Pipfile.lock"):
        PythonProject(install=True, lockfile="requirements.lock")
    with pytest.raises(ValueError, match="not used with Pipfile.lock"):
        PythonProject(install=True, lockfile="Pipfile.lock", pyproject="pyproject.toml")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("kind", ["poetry", "pipenv"])
def test_requirements_for_base_reads_the_base_lockfile_only(tmp_path, kind):
    repo = tmp_path / "repo"
    (repo / "svc").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    if kind == "poetry":
        files = {"svc/poetry.lock": POETRY_LOCK, "svc/pyproject.toml": PEP621}
        project = PythonProject(root="svc", install=True, lockfile="svc/poetry.lock")
    else:
        files = {"svc/Pipfile.lock": pipfile_lock({"attrs": entry("attrs")})}
        project = PythonProject(root="svc", install=True, lockfile="svc/Pipfile.lock")
    for path, text in files.items():
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    # A head commit that swaps the dependencies must not change what base installs.
    for path in files:
        (repo / path).write_text("not a lockfile")
    git(repo, "commit", "-qam", "head")
    text, index = requirements_for_base(repo, base, project)
    assert "attrs" in names(text)
    assert index == "https://pypi.org/simple"
