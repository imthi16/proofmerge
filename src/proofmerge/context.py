"""Extract the committed Git context for a base/head comparison."""

import hashlib
import os
import subprocess
from pathlib import Path

from pydantic import BaseModel


class ContextError(Exception):
    """Git context could not be extracted from the requested repository or refs."""


class GitContext(BaseModel):
    """Resolved revisions and their repository-relative changes."""

    base_sha: str
    head_sha: str
    changed_files: list[str]
    diff_text: str
    diff_sha256: str


def _git(repo_path: str | Path, *args: str, error: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repo_path), *args],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ContextError(f"Cannot run Git: {exc.strerror or str(exc)}") from None
    if result.returncode != 0:
        raise ContextError(error) from None
    return result.stdout


def get_context(repo_path: str | Path, base_ref: str, head_ref: str) -> GitContext:
    """Compare two commits, raising ContextError for repository or ref errors."""
    for ref in (base_ref, head_ref):
        if ref.startswith("-"):
            raise ContextError(f"Invalid ref {ref!r}: refs must not start with '-'.")

    _git(repo_path, "rev-parse", "--git-dir", error=f"Not a Git repository: {repo_path}")
    shas = [
        _git(
            repo_path,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{ref}^{{commit}}",
            error=f"Unknown ref or not a commit: {ref!r}",
        )
        .decode("ascii")
        .strip()
        for ref in (base_ref, head_ref)
    ]
    diff_args = (
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--no-relative",
        "--no-renames",
    )
    changed_files = _git(
        repo_path,
        *diff_args,
        "--name-only",
        "-z",
        *shas,
        "--",
        error="Could not list changed files.",
    )
    diff_bytes = _git(
        repo_path,
        *diff_args,
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--unified=3",
        *shas,
        "--",
        error="Could not extract the Git diff.",
    )
    return GitContext(
        base_sha=shas[0],
        head_sha=shas[1],
        changed_files=[os.fsdecode(path) for path in changed_files.split(b"\0") if path],
        diff_text=diff_bytes.decode("utf-8", errors="replace"),
        # Hash the raw bytes: lossy decoding could make two different diffs look identical.
        diff_sha256=hashlib.sha256(diff_bytes).hexdigest(),
    )
