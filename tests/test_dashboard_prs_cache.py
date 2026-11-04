"""Pull request verification must use the current head, not a cached one.

After a suggested fix is applied to a PR, the next verify must refresh the PR list
so the run is named for and associated with the new head commit.
"""

import subprocess
from pathlib import Path

import pytest

from proofmerge.dashboard_prs import PullRequests


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    (path / "a.py").write_text("x = 1\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    (path / "a.py").write_text("x = 2\n")
    git(path, "commit", "-qam", "head")
    return path


class FakeRunner:
    def __init__(self, prs_data):
        self.prs_data = prs_data
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(argv, 0, '{"nameWithOwner": "owner/repo", "url": "https://github.com/owner/repo"}', "")
        if argv[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(argv, 0, self.prs_data.pop(0), "")
        if argv[1] == "check-ref-format":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_start_refreshes_pr_list_so_run_uses_current_head(repo: Path, tmp_path: Path,
                                                           monkeypatch) -> None:
    old_sha = "0000000000000000000000000000000000000000"
    new_sha = git(repo, "rev-parse", "HEAD")
    pr_json = '[{"number": 1, "title": "t", "author": {"login": "a"}, "headRefName": "feature", "baseRefName": "main", "headRefOid": "%s", "state": "OPEN", "url": "https://github.com/owner/repo/pull/1", "updatedAt": "2026-11-04T00:00:00Z", "isDraft": false, "body": ""}]'
    runner = FakeRunner([pr_json % old_sha, pr_json % new_sha])
    prs = PullRequests(repo, tmp_path, runner=runner)

    # First call caches the PR list with the old head.
    assert prs.pulls()[0]["head_sha"] == old_sha

    # Capture what _verify receives without running the real verification.
    captured = {}

    def fake_verify(pr, run_id):
        captured["pr"] = pr
        captured["run_id"] = run_id

    monkeypatch.setattr(prs, "_verify", fake_verify)

    job = prs.start(1)

    # start() must refresh the PR list to pick up the new head_sha.
    assert captured["pr"]["head_sha"] == new_sha
    assert job["head_sha"] == new_sha
    assert new_sha[:7] in job["run"]
    # Two gh pr list calls: the first cache and the refresh in start().
    assert len([c for c in runner.calls if c[:3] == ["gh", "pr", "list"]]) == 2
