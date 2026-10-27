import hashlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from proofmerge.context import ContextError, GitContext, get_context


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
    ).stdout.decode()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "ProofMerge Tests")
    git(tmp_path, "config", "user.email", "proofmerge@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "example.txt").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "Base")
    base = git(tmp_path, "rev-parse", "HEAD").strip()
    git(tmp_path, "tag", "base")
    (tmp_path / "example.txt").write_text("after\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "Head")
    head = git(tmp_path, "rev-parse", "HEAD").strip()
    return tmp_path, base, head


def test_get_context(repo: tuple[Path, str, str]) -> None:
    path, base, head = repo
    context = get_context(path, "base", "HEAD")

    assert isinstance(context, GitContext)
    assert context.base_sha == base
    assert context.head_sha == head
    assert context.changed_files == ["example.txt"]
    assert context.diff_text == (
        "diff --git a/example.txt b/example.txt\n"
        "index 90be1f3..294186e 100644\n"
        "--- a/example.txt\n"
        "+++ b/example.txt\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+after\n"
    )
    assert context.diff_sha256 == hashlib.sha256(context.diff_text.encode()).hexdigest()


def test_ignores_uncommitted_changes(repo: tuple[Path, str, str]) -> None:
    path, base, head = repo
    expected = get_context(path, base, head)
    (path / "example.txt").write_text("uncommitted\n")
    (path / "untracked.txt").write_text("untracked\n")

    assert get_context(str(path), base, head) == expected


def test_same_revision(repo: tuple[Path, str, str]) -> None:
    path, base, _ = repo
    context = get_context(path, base, base)

    assert context.base_sha == context.head_sha == base
    assert context.changed_files == []
    assert context.diff_text == ""
    assert context.diff_sha256 == hashlib.sha256(b"").hexdigest()


def test_changed_paths_include_additions_deletions_and_newlines(
    repo: tuple[Path, str, str],
) -> None:
    path, base, _ = repo
    unusual_name = "new\tfile\n.txt"
    (path / "example.txt").unlink()
    (path / unusual_name).write_text("new file\n")
    git(path, "add", "-A")
    git(path, "commit", "-m", "Add and delete files")

    context = get_context(path, base, "HEAD")

    assert context.changed_files == ["example.txt", unusual_name]
    assert "deleted file mode" in context.diff_text
    assert "new file mode" in context.diff_text


@pytest.mark.parametrize("missing", [False, True])
def test_not_a_repo(tmp_path: Path, missing: bool) -> None:
    path = tmp_path / "missing" if missing else tmp_path
    with pytest.raises(ContextError, match="Not a Git repository"):
        get_context(path, "HEAD", "HEAD")


@pytest.mark.parametrize("base_ref,head_ref", [("missing", "HEAD"), ("HEAD", "missing")])
def test_unknown_ref(
    repo: tuple[Path, str, str],
    base_ref: str,
    head_ref: str,
) -> None:
    path, _, _ = repo
    with pytest.raises(ContextError, match="Unknown ref or not a commit: 'missing'"):
        get_context(path, base_ref, head_ref)


@pytest.mark.parametrize("base_ref,head_ref", [("--help", "HEAD"), ("HEAD", "-x")])
def test_option_ref_rejected_before_git(tmp_path: Path, base_ref: str, head_ref: str) -> None:
    with patch("proofmerge.context.subprocess.run") as run:
        with pytest.raises(ContextError, match="refs must not start with '-'"):
            get_context(tmp_path, base_ref, head_ref)
        run.assert_not_called()


def test_ref_must_resolve_to_commit(repo: tuple[Path, str, str]) -> None:
    path, _, _ = repo
    with pytest.raises(ContextError, match="Unknown ref or not a commit"):
        get_context(path, "HEAD:example.txt", "HEAD")


def test_git_unavailable(tmp_path: Path) -> None:
    with (
        patch("proofmerge.context.subprocess.run", side_effect=FileNotFoundError("git")),
        pytest.raises(ContextError, match="Cannot run Git"),
    ):
        get_context(tmp_path, "HEAD", "HEAD")
