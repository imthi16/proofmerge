"""Pull requests for the dashboard: list them with `gh`, and verify one on request.

Verifying runs `git fetch` into refs/proofmerge/* (never touching the user's branches)
and then `proofmerge verify` as a child process, so the PR's code only ever runs in the
Docker sandbox. Commands are argument lists, never a shell. One job runs at a time.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PR_FIELDS = "number,title,author,headRefName,baseRefName,headRefOid,state,url,updatedAt,isDraft,body"
PR_LIMIT = 50
CACHE_SECONDS = 60
VERIFY_TIMEOUT = 1800
MAX_BODY = 20_000
REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
Runner = Callable[..., Any]


class PrError(Exception):
    """A failure safe to show in the page."""


def _valid_branch(repo: Path, name: str, runner: Runner) -> bool:
    if not name or name.startswith("-"):
        return False
    result = runner(["git", "-C", str(repo), "check-ref-format", "--branch", name],
                    capture_output=True, text=True, timeout=10)
    return result.returncode == 0


class PullRequests:
    def __init__(self, repo: Path, evidence_root: Path, runner: Runner = subprocess.run) -> None:
        self.repo = repo.resolve()
        self.root = evidence_root.resolve()
        self._run = runner
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._remote: tuple[str, str] | None = None  # (nameWithOwner, clone url) of what gh lists
        self._lock = threading.Lock()
        self.job: dict[str, Any] | None = None

    def _repository(self) -> tuple[str, str]:
        """The repository gh lists PRs from; fetching from it keeps list and fetch in step."""
        if self._remote:
            return self._remote
        try:
            result = self._run(["gh", "repo", "view", "--json", "nameWithOwner,url"],
                               cwd=self.repo, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            raise PrError("The GitHub CLI (gh) is not installed.") from None
        except subprocess.TimeoutExpired:
            raise PrError("gh repo view timed out.") from None
        try:
            if result.returncode != 0:
                raise ValueError
            info = json.loads(result.stdout)
            name, url = str(info["nameWithOwner"]), str(info["url"])
            if not REPO_NAME.fullmatch(name) or not url.startswith("https://"):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise PrError("gh repo view failed. Check `gh auth status` and that the repo has a GitHub remote.") from None
        self._remote = (name, url)
        return self._remote

    def pulls(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._cache and not refresh and time.monotonic() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        name, _ = self._repository()
        try:
            result = self._run(
                ["gh", "pr", "list", "--repo", name, "--state", "all", "--limit", str(PR_LIMIT), "--json", PR_FIELDS],
                cwd=self.repo, capture_output=True, text=True, timeout=30,
            )
        except FileNotFoundError:
            raise PrError("The GitHub CLI (gh) is not installed.") from None
        except subprocess.TimeoutExpired:
            raise PrError("gh pr list timed out.") from None
        if result.returncode != 0:
            # gh's stderr can name accounts or hosts; keep the page message generic.
            raise PrError("gh pr list failed. Check `gh auth status` and that the repo has a GitHub remote.")
        try:
            prs = [
                {
                    "number": int(p["number"]), "title": str(p["title"]),
                    "author": str((p.get("author") or {}).get("login", "")),
                    "head": str(p["headRefName"]), "base": str(p["baseRefName"]),
                    "head_sha": str(p["headRefOid"]), "state": str(p["state"]),
                    "draft": bool(p.get("isDraft")), "url": str(p["url"]),
                    "updated": str(p.get("updatedAt", "")), "body": str(p.get("body") or "")[:MAX_BODY],
                }
                for p in json.loads(result.stdout)
            ]
        except (ValueError, KeyError, TypeError):
            raise PrError("gh pr list returned an unexpected format.") from None
        self._cache = (time.monotonic(), prs)
        return prs

    def start(self, number: int) -> dict[str, Any]:
        pr = next((p for p in self.pulls() if p["number"] == number), None)
        if pr is None:
            raise PrError(f"PR #{number} is not in the list.")
        if pr["state"] != "OPEN":
            # Its base branch already contains it, so base..head would be empty.
            raise PrError(f"PR #{number} is {pr['state'].lower()}; only open pull requests can be verified.")
        if not _valid_branch(self.repo, pr["base"], self._run):
            raise PrError(f"PR #{number} has an unusable base branch name.")
        with self._lock:
            if self.job and self.job["status"] == "running":
                raise PrError(f"PR #{self.job['number']} is still being verified.")
            run_id = f"pr-{number}-{pr['head_sha'][:7]}-{int(time.time())}"
            self.job = {"number": number, "status": "running", "step": "Fetching the PR",
                        "run": run_id, "exit_code": None, "output": ""}
        threading.Thread(target=self._verify, args=(pr, run_id), daemon=True).start()
        return self.job

    def _step(self, argv: list[str], timeout: int, env: dict[str, str] | None = None) -> Any:
        extra = {} if env is None else {"env": env}
        return self._run(argv, capture_output=True, text=True, timeout=timeout, **extra)

    def _kill_containers(self, run_id: str) -> None:
        """Killing `proofmerge verify` leaves its sandbox containers running; stop them by label."""
        listed = self._run(["docker", "ps", "-q", "--filter", f"label=proofmerge.job={run_id}"],
                           capture_output=True, text=True, timeout=30, check=False)
        for container in (listed.stdout or "").split():
            self._run(["docker", "kill", container], capture_output=True, timeout=30, check=False)

    def _verify(self, pr: dict[str, Any], run_id: str) -> None:
        n = pr["number"]
        head, base = f"refs/proofmerge/pr-{n}", f"refs/proofmerge/base-{n}"
        job = self.job
        try:
            fetched = self._step(["git", "-C", str(self.repo), "fetch", "--no-tags", f"{self._repository()[1]}.git",
                                  f"+refs/pull/{n}/head:{head}", f"+refs/heads/{pr['base']}:{base}"], 300)
            if fetched.returncode != 0:
                job.update(status="failed", step="git fetch failed", exit_code=fetched.returncode)
                return
            job["step"] = "Running proofmerge verify (a few minutes)"
            with tempfile.TemporaryDirectory(prefix="proofmerge-pr-") as tmp:
                intent = Path(tmp) / "intent.md"
                intent.write_text(f"{pr['title']}\n\n{pr['body']}", encoding="utf-8")
                done = self._step([
                    sys.executable, "-m", "proofmerge.cli", "verify", str(self.repo),
                    "--base", base, "--head", head, "--intent-file", str(intent),
                    "--evidence-dir", str(self.root / run_id),
                ], VERIFY_TIMEOUT, {**os.environ, "PROOFMERGE_JOB_ID": run_id})
            # verify exits 0 for PASS/REVIEW and 2 for BLOCK; 1 means it could not run.
            # Exit 0 without a proof means there was nothing to check.
            ok = done.returncode in (0, 2) and (self.root / run_id / "proof.json").is_file()
            job.update(status="done" if ok else "failed",
                       step="Finished" if ok else "verify did not produce a proof",
                       exit_code=done.returncode,
                       output=((done.stdout or "") + (done.stderr or ""))[-4000:])
        except subprocess.TimeoutExpired:
            try:
                self._kill_containers(run_id)
                step = "Timed out; its containers were stopped"
            except (OSError, subprocess.SubprocessError):
                step = "Timed out; its containers may still be running (check `docker ps`)"
            job.update(status="failed", step=step)
        except OSError as error:
            job.update(status="failed", step=f"Could not start: {type(error).__name__}")
