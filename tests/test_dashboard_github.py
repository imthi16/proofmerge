"""GitHub sign-in, repo list and clone for the app, against a fake gh: no network, no account."""

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.dashboard import make_server
from proofmerge.dashboard_github import DEVICE_URL, GH_CREDENTIALS, GitHub, GitHubError, repo_name


class FakeGh:
    """Runner and spawn for GitHub(): `gh api user` answers with `self.user`."""

    def __init__(self, user: str | None = None, installed: bool = True) -> None:
        self.user, self.installed = user, installed
        self.offline = self.revoked = False
        self.calls: list[list[str]] = []
        self.answers: dict[str, SimpleNamespace] = {}
        self.lines: queue.Queue = queue.Queue()
        self.exit = 0
        self.killed = threading.Event()
        self.spawned: tuple[list[str], dict] | None = None

    def run(self, argv: list[str], **_: object) -> SimpleNamespace:
        if not self.installed:
            raise FileNotFoundError(argv[0])
        self.calls.append(argv)
        if argv[:3] == ["gh", "api", "user"]:
            if self.offline:
                return SimpleNamespace(returncode=1, stdout="", stderr="error connecting to api.github.com")
            if self.revoked:
                return SimpleNamespace(returncode=1, stdout="", stderr="gh: Bad credentials (HTTP 401)")
            # gh exits 4 when nobody is signed in.
            return SimpleNamespace(returncode=0 if self.user else 4, stdout=f"{self.user or ''}\n", stderr="")
        for prefix, answer in self.answers.items():
            if " ".join(argv).startswith(prefix):
                return answer
        return SimpleNamespace(returncode=0, stdout="")

    def spawn(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
        self.spawned = (argv, kwargs)

        def stdout():
            while (line := self.lines.get()) is not None:
                yield line

        def kill() -> None:
            self.killed.set()
            self.lines.put(None)

        def wait() -> int:
            return -9 if self.killed.is_set() else self.exit

        return SimpleNamespace(stdout=stdout(), kill=kill, wait=wait)


def settle(predicate, seconds: float = 2.0) -> None:
    deadline = time.time() + seconds
    while not predicate():
        assert time.time() < deadline, "timed out"
        time.sleep(0.01)


@pytest.mark.parametrize("bad", ["octocat", "a/b/c", "o/..", "o/.", "o/a b", "-/x/y", "o/x;rm",
                                 "https://github.com/o/r", "", None, 7, "o" * 40 + "/r"])
def test_repo_names_are_owner_slash_name_only(bad: object) -> None:
    with pytest.raises(GitHubError):
        repo_name(bad)


def test_repo_name_accepts_real_names() -> None:
    assert repo_name("octo-cat/hello.world_1") == "octo-cat/hello.world_1"


def test_sign_in_shows_the_code_and_finishes_when_gh_exits(tmp_path: Path) -> None:
    gh = FakeGh()
    github = GitHub(tmp_path, gh.run, gh.spawn)
    github.start_login()
    argv, kwargs = gh.spawned
    assert argv == ["gh", "auth", "login", "--web", "--hostname", "github.com", "--git-protocol", "https"]
    assert "GH_PROMPT_DISABLED" not in kwargs["env"]  # gh must still print the code
    assert github.status()["login"] == {"state": "waiting", "code": None, "url": DEVICE_URL, "error": None}

    gh.lines.put("\n")
    gh.lines.put("! First copy your one-time code: AB12-CD34\n")
    gh.lines.put("Open this URL to continue in your web browser: https://evil.example/login\n")
    settle(lambda: github.login["code"] == "AB12-CD34")
    assert github.status()["login"]["url"] == DEVICE_URL  # never a URL from gh's output

    gh.user = "octocat"
    gh.lines.put(None)
    settle(lambda: github.login["state"] == "done")
    status = github.status()
    assert status["user"] == "octocat" and status["login"]["code"] is None


def test_sign_in_is_refused_when_signed_in_or_gh_is_missing(tmp_path: Path) -> None:
    with pytest.raises(GitHubError, match="Already signed in as octocat"):
        GitHub(tmp_path, FakeGh("octocat").run).start_login()
    missing = FakeGh(installed=False)
    with pytest.raises(GitHubError, match="cli.github.com"):
        GitHub(tmp_path, missing.run, missing.spawn).start_login()
    assert GitHub(tmp_path, missing.run).status()["gh"] is False


def test_cancel_stops_gh_and_a_failed_sign_in_says_why_without_gh_output(tmp_path: Path) -> None:
    gh = FakeGh()
    github = GitHub(tmp_path, gh.run, gh.spawn)
    github.start_login()
    with pytest.raises(GitHubError, match="already waiting"):
        github.start_login()
    github.cancel_login()
    assert gh.killed.is_set()
    time.sleep(0.05)
    assert github.login["state"] == "cancelled"

    gh2 = FakeGh()
    gh2.exit = 1
    failing = GitHub(tmp_path, gh2.run, gh2.spawn)
    failing.start_login()
    gh2.lines.put("error: secret-account-name expired\n")
    gh2.lines.put(None)
    settle(lambda: failing.login["state"] == "failed")
    assert "15 minutes" in failing.login["error"] and "secret" not in failing.login["error"]


def test_a_network_problem_is_not_shown_as_signed_out(tmp_path: Path) -> None:
    gh = FakeGh("octocat")
    gh.offline = True
    github = GitHub(tmp_path, gh.run, gh.spawn)
    status = github.status()
    assert status["user"] is None and "Could not reach GitHub" in status["error"]
    for action in (github.start_login, github.repos, lambda: github.start_clone("octocat/hello")):
        with pytest.raises(GitHubError, match="Could not reach GitHub"):
            action()
    assert gh.spawned is None and github.clone is None
    gh.offline, gh.revoked = False, True  # a revoked token means signed out, not offline
    assert GitHub(tmp_path, gh.run).status() == {
        "gh": True, "user": None, "error": None, "login": None, "clone": None}


def test_repos_lists_valid_names_and_needs_sign_in(tmp_path: Path) -> None:
    gh = FakeGh()
    with pytest.raises(GitHubError, match="Sign in"):
        GitHub(tmp_path, gh.run).repos()
    gh.user = "octocat"
    rows = [{"full_name": "octocat/hello", "description": None, "private": True, "updated_at": "2026-11-01T00:00:00Z"},
            {"full_name": "bad name/x"}]
    gh.answers["gh api user/repos"] = SimpleNamespace(returncode=0, stdout=json.dumps(rows))
    assert GitHub(tmp_path, gh.run).repos() == [
        {"name": "octocat/hello", "description": "", "private": True, "updated": "2026-11-01T00:00:00Z"}]


def test_clone_into_the_repos_folder_then_fetch_on_the_next_pick(tmp_path: Path) -> None:
    gh = FakeGh("octocat")
    github = GitHub(tmp_path, gh.run)
    with pytest.raises(GitHubError, match="owner/name"):
        github.start_clone("../etc")
    github.start_clone("octocat/hello")
    settle(lambda: github.clone["state"] != "running")
    target = tmp_path / "octocat" / "hello"
    assert gh.calls[-1] == ["gh", "repo", "clone", "octocat/hello", str(target)]
    assert github.clone == {"repo": "octocat/hello", "state": "done", "path": str(target), "error": None}

    (target / ".git").mkdir(parents=True)
    github.start_clone("octocat/hello")
    settle(lambda: github.clone["state"] != "running")
    assert gh.calls[-1] == ["git", "-C", str(target), *GH_CREDENTIALS, "fetch", "--prune", "origin"]


def test_clone_failures_are_generic_and_never_overwrite_a_folder(tmp_path: Path) -> None:
    gh = FakeGh("octocat")
    gh.answers["gh repo clone"] = SimpleNamespace(returncode=1, stdout="fatal: token ghp_secret")
    github = GitHub(tmp_path, gh.run)
    github.start_clone("octocat/missing")
    settle(lambda: github.clone["state"] != "running")
    assert github.clone["state"] == "failed" and "ghp_" not in github.clone["error"]

    (tmp_path / "octocat" / "notes").mkdir(parents=True)
    github.start_clone("octocat/notes")
    settle(lambda: github.clone["state"] != "running")
    assert "is not a Git repository" in github.clone["error"]
    assert not any(c[:3] == ["gh", "repo", "clone"] and c[3] == "octocat/notes" for c in gh.calls)


def call(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_routes_need_the_session_token(tmp_path: Path) -> None:
    gh = FakeGh("octocat")
    httpd = make_server(tmp_path, 0, github=GitHub(tmp_path / "repos", gh.run, gh.spawn))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        token = call(base + "/api/session")[1]["token"]
        assert call(base + "/api/github")[0] == 403
        assert call(base + "/api/github/repos")[0] == 403
        auth = {"X-ProofMerge-Token": token}
        assert call(base + "/api/github", headers=auth) == (
            200, {"gh": True, "user": "octocat", "error": None, "login": None, "clone": None})
        post = {"Content-Type": "application/json", "Origin": base}
        clone = json.dumps({"repo": "octocat/hello"}).encode()
        assert call(base + "/api/github/clone", "POST", post, clone)[0] == 403
        status, reply = call(base + "/api/github/clone", "POST", post | auth, b'{"repo": "a/../b"}')
        assert status == 422 and "owner/name" in reply["error"]
        status, reply = call(base + "/api/github/clone", "POST", post | auth, clone)
        assert status == 202 and reply["clone"]["repo"] == "octocat/hello"
        status, reply = call(base + "/api/github/login", "POST", post | auth, b"{}")
        assert status == 422 and "Already signed in" in reply["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()
