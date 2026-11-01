"""Lockfile planning, marker evaluation and wheel install. No network and no Docker."""

import hashlib
import io
import zipfile

import pytest

from proofmerge.project_config import PythonProject
from proofmerge.python_layout import (
    LayoutError,
    eval_marker,
    install_hashed_wheels,
    marker_environment,
    parse_requirements,
    plan_uv_lock,
    render_requirements,
    resolve_inside,
    validate_index_url,
)

MARKERS = [
    ("python_full_version < '3.13'", False),
    ("sys_platform == 'win32'", False),
    (
        (
            "platform_machine == 'AMD64' or platform_machine == 'aarch64' "
            "or platform_machine == 'amd64' or platform_machine == 'arm64' "
            "or platform_machine == 'x86_64'"
        ),
        True,
    ),
    ("platform_python_implementation != 'PyPy'", True),
    ("sys_platform == 'emscripten' or sys_platform == 'win32'", False),
    ("implementation_name != 'pypy'", True),
    (
        (
            "platform_python_implementation != 'PyPy' and sys_platform != 'cygwin' "
            "and sys_platform != 'win32'"
        ),
        True,
    ),
    ("(sys_platform == 'linux')", True),
]


def test_helpdeck_style_markers_match_the_linux_runner():
    env = marker_environment("3.13", machine="x86_64")
    for marker, expected in MARKERS:
        assert eval_marker(marker, env) is expected
    narrow = marker_environment("3.13", machine="riscv64")
    assert eval_marker("platform_machine == 'x86_64' or platform_machine == 'aarch64'", narrow) is False


def test_marker_rejects_unknown_variables_and_bad_syntax():
    env = marker_environment("3.13", machine="x86_64")
    with pytest.raises(LayoutError, match="unsupported"):
        eval_marker("platform_release == '1'", env)
    with pytest.raises(LayoutError, match="ended early"):
        eval_marker("sys_platform ==", env)


def test_index_url_rejects_credentials():
    assert validate_index_url("https://pypi.org/simple") == "https://pypi.org/simple"
    with pytest.raises(LayoutError, match="credentials"):
        validate_index_url("https://token@pypi.org/simple")
    with pytest.raises(LayoutError, match="https"):
        validate_index_url("http://pypi.org/simple")


def test_requirements_must_be_pinned_and_hashed():
    digest = "a" * 64
    parsed = parse_requirements(f"greetlib==1.0.0 --hash=sha256:{digest}\n")
    assert parsed == [("greetlib", "1.0.0", [digest])]
    with pytest.raises(LayoutError, match="hash"):
        parse_requirements("greetlib==1.0.0\n")
    with pytest.raises(LayoutError, match="URLs"):
        parse_requirements(f"greetlib @ https://example.invalid/g.whl --hash=sha256:{digest}\n")
    rendered = render_requirements(parsed)
    assert parse_requirements(rendered) == parsed


def _lock(digest: str, *, source: str = '{ registry = "https://pypi.org/simple" }') -> str:
    return f"""
version = 1
[[package]]
name = "demo-api"
version = "0.1.0"
source = {{ virtual = "." }}
dependencies = [{{ name = "greetlib" }}]

[[package]]
name = "greetlib"
version = "1.0.0"
source = {source}
wheels = [{{ url = "https://example.invalid/greetlib.whl", hash = "sha256:{digest}" }}]
"""


def test_uv_lock_plans_hashed_wheels_and_skips_the_project(tmp_path):
    digest = "b" * 64
    pyproject = '[project]\nname = "demo-api"\ndependencies = ["greetlib==1.0.0"]\n'
    text, index = plan_uv_lock(_lock(digest), pyproject, "3.13")
    assert index == "https://pypi.org/simple"
    assert parse_requirements(text) == [("greetlib", "1.0.0", [digest])]
    assert "demo-api" not in text
    assert "example.invalid" not in text


def test_uv_lock_refuses_git_sources_and_sdists():
    with pytest.raises(LayoutError, match="non-registry"):
        plan_uv_lock(_lock("c" * 64, source='{ git = "https://example.invalid/g" }'), None, "3.13")
    sdist_only = _lock("d" * 64).replace(
        'wheels = [{ url = "https://example.invalid/greetlib.whl", hash = "sha256:' + "d" * 64 + '" }]',
        'sdist = { url = "https://example.invalid/g.tar.gz", hash = "sha256:' + "d" * 64 + '" }',
    )
    with pytest.raises(LayoutError, match="no wheels"):
        plan_uv_lock(sdist_only, None, "3.13")


def test_pyproject_dependency_missing_from_the_lock_is_refused():
    with pytest.raises(LayoutError, match="not in the base"):
        plan_uv_lock(_lock("e" * 64), '[project]\ndependencies = ["other"]\n', "3.13")


def test_install_hashed_wheels_unpacks_only_a_matching_hash(tmp_path):
    payload = b"def hello(name):\n    return f'hello {name}'\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("greetlib/__init__.py", payload)
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    wheel = tmp_path / "greetlib-1.0.0-py3-none-any.whl"
    wheel.write_bytes(data)
    dest = tmp_path / "dest"
    dest.mkdir()
    install_hashed_wheels(render_requirements([("greetlib", "1.0.0", [digest])]), tmp_path, dest)
    assert (dest / "greetlib" / "__init__.py").read_bytes() == payload
    with pytest.raises(LayoutError, match="no local wheel"):
        install_hashed_wheels(
            render_requirements([("greetlib", "1.0.0", ["f" * 64])]), tmp_path, tmp_path / "other"
        )


def test_wheel_with_parent_path_is_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("../evil.py", b"x = 1\n")
    data = buf.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "bad-1.0.0-py3-none-any.whl").write_bytes(data)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(LayoutError, match="unsafe"):
        install_hashed_wheels(render_requirements([("bad", "1.0.0", [digest])]), tmp_path, dest)
    assert not (tmp_path / "evil.py").exists()


def test_probe_prompt_keeps_the_default_text_until_a_root_is_set():
    from proofmerge.probes import PROMPT, _probe_prompt

    assert _probe_prompt(None, None) == PROMPT
    noted = _probe_prompt(None, PythonProject(root="apps/api"))
    assert noted.startswith(PROMPT)
    assert "apps/api" in noted
    assert "not from the pull request" in noted


def test_resolve_inside_rejects_symlinks(tmp_path):
    code = tmp_path / "code"
    (code / "apps").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (code / "apps" / "api").symlink_to(outside)
    with pytest.raises(LayoutError, match="symlink"):
        resolve_inside(code, "apps/api")
    real = code / "apps" / "real"
    real.mkdir()
    assert resolve_inside(code, "apps/real") == real
    project = PythonProject(root="apps/real")
    assert project.root == "apps/real"
