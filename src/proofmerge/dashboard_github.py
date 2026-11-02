"""GitHub sign-in, repository list and clone for the app, all through the GitHub CLI.

gh keeps the token in the system credential store; ProofMerge never reads, logs or stores it.
Sign-in runs `gh auth login --web` with no terminal: gh prints a one-time code and waits until
the user enters it at github.com/login/device. Cloning never runs the repository's code; only
`verify` does, inside the Docker sandbox. Commands are argument lists, never a shell.
"""

import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEVICE_URL = "https://github.com/login/device"
CODE = re.compile(r"one-time code: ([A-Z0-9]{4}-[A-Z0-9]{4})")
REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
LOGIN = re.compile(r"[A-Za-z0-9-]{1,39}")
LOGIN_SECONDS = 900  # GitHub device codes expire after 15 minutes
CLONE_SECONDS = 900
UNREACHABLE = "Could not reach GitHub. Check the internet connection, then try again."
# Lets git use gh's sign-in for github.com for one command, without changing the user's git config.
GH_CREDENTIALS = ["-c", "credential.https://github.com.helper=",
                  "-c", "credential.https://github.com.helper=!gh auth git-credential"]
Runner = Callable[..., Any]


class GitHubError(Exception):
    """A failure safe to show in the page."""


def repo_name(value: Any) -> str:
    if not isinstance(value, str) or not REPO.fullmatch(value) or value.split("/")[1] in (".", ".."):
        raise GitHubError("Use the form owner/name, like octocat/hello-world.")
    return value


def _env() -> dict[str, str]:
    # No prompts: a question nobody can answer would hang the request.
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
            "GIT_TERMINAL_PROMPT": "0"}


class GitHub:
    def __init__(self, repos_root: Path, runner: Runner = subprocess.run,
                 spawn: Runner = subprocess.Popen) -> None:
        self.root = repos_root
        self._run = runner
        self._spawn = spawn
        self._lock = threading.Lock()
        self._process: Any = None
        self.login: dict[str, Any] | None = None
        self.clone: dict[str, Any] | None = None

    def _gh(self, *argv: str, timeout: int = 20) -> Any:
        return self._run(["gh", *argv], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=timeout, env=_env())

    def _who(self) -> tuple[bool, str | None]:
        """(gh installed, signed-in login or None). Raises when GitHub cannot be reached,
        so a network problem is never shown as "signed out"."""
        try:
            result = self._gh("api", "user", "--jq", ".login", timeout=15)
        except FileNotFoundError:
            return False, None
        except subprocess.TimeoutExpired:
            raise GitHubError(UNREACHABLE) from None
        name = (result.stdout or "").strip()
        if result.returncode == 0 and LOGIN.fullmatch(name):
            return True, name
        # gh exits 4 when nobody is signed in; a revoked token answers HTTP 401.
        if result.returncode == 4 or "HTTP 401" in (result.stderr or ""):
            return True, None
        raise GitHubError(UNREACHABLE)

    def status(self) -> dict[str, Any]:
        waiting = self.login is not None and self.login["state"] == "waiting"
        error = None
        # shortcut: one `gh api user` call per status read; cache it if polling gets slow.
        try:
            installed, user = (True, None) if waiting else self._who()
        except GitHubError as problem:
            installed, user, error = True, None, str(problem)
        return {"gh": installed, "user": user, "error": error, "login": self.login and dict(self.login),
                "clone": self.clone and dict(self.clone)}

    # Sign-in ----------------------------------------------------------------------------
    def start_login(self) -> None:
        with self._lock:
            if self.login is not None and self.login["state"] == "waiting":
                raise GitHubError("Sign-in is already waiting for its code.")
            installed, user = self._who()
            if not installed:
                raise GitHubError("Install the GitHub CLI first: https://cli.github.com/")
            if user:
                raise GitHubError(f"Already signed in as {user}.")
            # Without a terminal gh skips its questions, prints the code and waits.
            process = self._spawn(
                ["gh", "auth", "login", "--web", "--hostname", "github.com", "--git-protocol", "https"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env={k: v for k, v in _env().items() if k != "GH_PROMPT_DISABLED"})
            self._process = process
            self.login = {"state": "waiting", "code": None, "url": DEVICE_URL, "error": None}
            threading.Thread(target=self._watch_login, args=(process, self.login), daemon=True).start()

    def _watch_login(self, process: Any, login: dict[str, Any]) -> None:
        timer = threading.Timer(LOGIN_SECONDS, process.kill)
        timer.start()
        try:
            for line in process.stdout:
                found = CODE.search(line)
                if found:
                    login["code"] = found.group(1)
            code = process.wait()
        finally:
            timer.cancel()
        with self._lock:
            if login["state"] != "waiting":
                return  # cancelled
            if code == 0:
                login.update(state="done", code=None)
            else:
                login.update(state="failed", code=None,
                             error="GitHub sign-in did not finish. The code works for 15 minutes; try again.")

    def cancel_login(self) -> None:
        with self._lock:
            if self.login is not None and self.login["state"] == "waiting":
                self.login.update(state="cancelled", code=None)
                self._process.kill()

    def logout(self) -> None:
        installed, user = self._who()
        if not installed or user is None:
            return
        result = self._gh("auth", "logout", "--hostname", "github.com", "--user", user)
        if result.returncode != 0:
            raise GitHubError("Could not sign out of GitHub.")
        self.login = None

    # Repositories -----------------------------------------------------------------------
    def repos(self) -> list[dict[str, Any]]:
        installed, user = self._who()
        if not installed or user is None:
            raise GitHubError("Sign in to GitHub first.")
        try:
            result = self._gh("api", "user/repos?per_page=100&sort=updated", timeout=30)
        except subprocess.TimeoutExpired:
            raise GitHubError("GitHub took too long to answer. Try again.") from None
        if result.returncode != 0:
            raise GitHubError("Could not list your repositories. Try again.")
        try:
            rows = json.loads(result.stdout)
            return [{"name": r["full_name"], "description": r.get("description") or "",
                     "private": bool(r.get("private")), "updated": r.get("updated_at") or ""}
                    for r in rows if REPO.fullmatch(str(r.get("full_name", "")))]
        except (ValueError, TypeError, KeyError):
            raise GitHubError("GitHub returned an unexpected answer.") from None

    def start_clone(self, value: Any) -> None:
        name = repo_name(value)
        with self._lock:
            if self.clone is not None and self.clone["state"] == "running":
                raise GitHubError(f"Still downloading {self.clone['repo']}.")
            installed, user = self._who()
            if not installed or user is None:
                raise GitHubError("Sign in to GitHub first.")
            self.clone = {"repo": name, "state": "running", "path": None, "error": None}
            threading.Thread(target=self._clone, args=(name, self.clone), daemon=True).start()

    def _clone(self, name: str, clone: dict[str, Any]) -> None:
        owner, repo = name.split("/")
        target = self.root / owner / repo
        try:
            if (target / ".git").is_dir():
                argv = ["git", "-C", str(target), *GH_CREDENTIALS, "fetch", "--prune", "origin"]
            elif target.exists():
                clone.update(state="failed", error=f"{target} exists and is not a Git repository.")
                return
            else:
                # A file at the repos root raises here; that must fail the clone, not leave it running.
                target.parent.mkdir(parents=True, exist_ok=True)
                argv = ["gh", "repo", "clone", name, str(target)]
            result = self._run(argv, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=CLONE_SECONDS, env=_env())
            ok = result.returncode == 0
            if ok:
                # A fork's parent becomes gh's default remote. Pin the repo the user picked.
                # --no-upstream does this on newer gh only; set-default works on 2.46 too.
                pinned = self._run(["gh", "repo", "set-default", name], cwd=str(target),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=30, env=_env())
                ok = pinned.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if ok:
            clone.update(state="done", path=str(target))
        else:
            # gh's own message can name accounts or hosts; keep the page message generic.
            clone.update(state="failed",
                         error=f"Could not download {name}. Check the name and that your GitHub account can see it.")
