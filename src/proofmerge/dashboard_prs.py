"""Pull requests for the dashboard: list them with `gh`, and verify one on request.

Verifying runs `git fetch` into refs/proofmerge/* (never touching the user's branches)
and then `proofmerge verify` as a child process, so the PR's code only ever runs in the
Docker sandbox. Commands are argument lists, never a shell. One job runs at a time.

Each job has an EventJournal (dashboard_events.py): the job's own steps, plus the stage
events `verify` writes to an --events-file in a temporary folder outside the evidence
directory. Job state is in memory: a restarted dashboard forgets running jobs, but
every finished run's evidence is on disk.
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

from proofmerge.dashboard_events import EventJournal, tail_events
from proofmerge.dashboard_github import GH_CREDENTIALS

PR_FIELDS = "number,title,author,headRefName,baseRefName,headRefOid,state,url,updatedAt,isDraft,body"
PR_LIMIT = 1000  # every open PR; the page searches them, it does not page
CACHE_SECONDS = 60
VERIFY_TIMEOUT = 1800
PONYTAIL_TIMEOUT = 900
MAX_BODY = 20_000
MAX_JOURNALS = 10  # finished jobs whose events stay readable
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
    def __init__(self, repo: Path, evidence_root: Path, runner: Runner = subprocess.run,
                 environment: Callable[[], dict[str, str]] | None = None) -> None:
        self.repo = repo.resolve()
        self.root = evidence_root.resolve()
        self._run = runner
        self._environment = environment or (lambda: dict(os.environ))
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._remote: tuple[str, str] | None = None  # (nameWithOwner, clone url) of what gh lists
        self._lock = threading.Lock()
        self.job: dict[str, Any] | None = None
        self.journals: dict[str, EventJournal] = {}  # job id -> events, oldest first
        self.pony: dict[str, Any] | None = None  # the Ponytail review's job, apart from verify's
        self.pony_post: dict[str, Any] | None = None  # posting its findings to the PR

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

    def forget(self) -> None:
        """Drop what GitHub answered for the previous account."""
        self._cache = self._remote = None

    def pulls(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._cache and not refresh and time.monotonic() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        name, _ = self._repository()
        try:
            result = self._run(
                ["gh", "pr", "list", "--repo", name, "--state", "open", "--limit", str(PR_LIMIT), "--json", PR_FIELDS],
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
        # Refresh the list so a verify started after a fix is applied uses the current head.
        pr = next((p for p in self.pulls(refresh=True) if p["number"] == number), None)
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
            journal = EventJournal(run_id, run_id)
            self.journals[run_id] = journal
            while len(self.journals) > MAX_JOURNALS:
                self.journals.pop(next(iter(self.journals)))
            self.job = {"number": number, "status": "running", "step": "Fetching the PR",
                        "run": run_id, "exit_code": None, "output": "", "job_id": run_id,
                        "head_sha": pr["head_sha"], "base_branch": pr["base"],
                        "created": time.time()}
            journal.append("job.created", status="running", message=f"Verification of PR #{number} started",
                           data={"pr_number": number})
        threading.Thread(target=self._verify, args=(pr, run_id), daemon=True).start()
        return self.job

    # Ponytail review: the diff only, so it needs no verify run, no Docker and one model call. ---------
    def ponytail_path(self, pr: dict[str, Any]) -> Path:
        return self.root / "ponytail" / f"pr-{pr['number']}-{pr['head_sha'][:7]}.json"

    def ponytail_report(self, pr: dict[str, Any]) -> dict[str, Any] | None:
        """The saved review of this PR at its current head commit, if there is one."""
        from proofmerge.ponytail import load_report_file

        report = load_report_file(self.ponytail_path(pr))
        return None if report is None else report.model_dump(mode="json")

    def start_ponytail(self, number: int, level: str = "low") -> dict[str, Any]:
        from proofmerge.ponytail import LEVELS

        if level not in LEVELS:
            raise PrError(f"The level must be one of {', '.join(LEVELS)}.")
        pr = next((p for p in self.pulls() if p["number"] == number), None)
        if pr is None:
            raise PrError(f"PR #{number} is not in the list.")
        if pr["state"] != "OPEN":
            raise PrError(f"PR #{number} is {pr['state'].lower()}; only open pull requests can be reviewed.")
        if not _valid_branch(self.repo, pr["base"], self._run):
            raise PrError(f"PR #{number} has an unusable base branch name.")
        with self._lock:
            if self.pony and self.pony["status"] == "running":
                raise PrError(f"The Ponytail review of PR #{self.pony['number']} is still running.")
            self.pony = {"number": number, "status": "running", "step": "Fetching the PR",
                         "head_sha": pr["head_sha"], "level": level, "error": None, "created": time.time()}
        threading.Thread(target=self._ponytail, args=(pr,), daemon=True).start()
        return self.pony

    def _ponytail(self, pr: dict[str, Any]) -> None:
        n = pr["number"]
        head, base = f"refs/proofmerge/pr-{n}", f"refs/proofmerge/base-{n}"
        job = self.pony

        def finish(status: str, step: str, error: str | None = None) -> None:
            job.update(status=status, step=step, error=error)

        try:
            fetched = self._step(["git", "-C", str(self.repo), *GH_CREDENTIALS, "fetch", "--no-tags",
                                  f"{self._repository()[1]}.git",
                                  f"+refs/pull/{n}/head:{head}", f"+refs/heads/{pr['base']}:{base}"], 300)
            if fetched.returncode != 0:
                finish("failed", "git fetch failed", "Could not fetch the pull request from GitHub.")
                return
            job["step"] = "Reading the diff (one model call)"
            out = self.ponytail_path(pr)
            with tempfile.TemporaryDirectory(prefix="proofmerge-pony-") as tmp:
                intent = Path(tmp) / "intent.md"
                intent.write_text(f"{pr['title']}\n\n{pr['body']}", encoding="utf-8")
                done = self._step([
                    sys.executable, "-m", "proofmerge.cli", "ponytail-diff", str(self.repo),
                    "--base", base, "--head", head, "--intent-file", str(intent), "--label", f"pr-{n}",
                    "--out", str(out), "--level", job["level"],
                ], PONYTAIL_TIMEOUT, self._environment())
            if done.returncode == 0 and out.is_file():
                finish("done", "Finished")
            else:
                tail = ((done.stderr or "") + (done.stdout or "")).strip()[-400:]
                finish("failed", "The review did not finish", tail or "The review did not produce a result.")
        except subprocess.TimeoutExpired:
            finish("failed", "Timed out", "The review took too long and was stopped.")
        except OSError as error:
            finish("failed", f"Could not start: {type(error).__name__}", "Could not start the review.")
        except PrError as error:
            finish("failed", str(error), str(error))

    def start_ponytail_post(self, number: int) -> dict[str, Any]:
        """Post this PR's saved Ponytail findings to GitHub as one review comment."""
        pr = next((p for p in self.pulls() if p["number"] == number), None)
        if pr is None:
            raise PrError(f"PR #{number} is not in the list.")
        if pr["state"] != "OPEN":
            raise PrError(f"PR #{number} is {pr['state'].lower()}; only open pull requests can be commented on.")
        report = self.ponytail_report(pr)
        if report is None or not report["findings"]:
            raise PrError("Review the pull request first; there are no findings to post.")
        if report.get("posted_url"):
            raise PrError("These findings were already posted. Review the pull request again to post new ones.")
        with self._lock:
            if self.pony_post and self.pony_post["status"] == "running":
                raise PrError("A post to GitHub is still running.")
            self.pony_post = {"number": number, "status": "running", "step": "Posting to GitHub",
                              "error": None, "created": time.time()}
        threading.Thread(target=self._ponytail_post, args=(pr,), daemon=True).start()
        return self.pony_post

    def _ponytail_post(self, pr: dict[str, Any]) -> None:
        job = self.pony_post
        try:
            done = self._step([sys.executable, "-m", "proofmerge.cli", "ponytail-post", str(self.ponytail_path(pr)),
                               "--pr", str(pr["number"]), "--repo", str(self.repo)], 180, self._environment())
            if done.returncode == 0:
                job.update(status="done", step="Posted", error=None)
            else:
                tail = ((done.stderr or "") + (done.stdout or "")).strip()[-400:]
                job.update(status="failed", step="Not posted", error=tail or "GitHub did not take the review.")
        except subprocess.TimeoutExpired:
            job.update(status="failed", step="Timed out", error="Posting took too long and was stopped.")
        except OSError as error:
            job.update(status="failed", step="Could not start", error=f"Could not start: {type(error).__name__}")

    def _step(self, argv: list[str], timeout: int, env: dict[str, str] | None = None) -> Any:
        extra = {} if env is None else {"env": env}
        return self._run(argv, capture_output=True, text=True, timeout=timeout, **extra)

    def _kill_containers(self, run_id: str) -> None:
        """Killing `proofmerge verify` leaves its sandbox containers running; stop them by label."""
        listed = self._run(["docker", "ps", "-q", "--filter", f"label=proofmerge.job={run_id}"],
                           capture_output=True, text=True, timeout=30, check=False)
        for container in (listed.stdout or "").split():
            self._run(["docker", "kill", container], capture_output=True, timeout=30, check=False)

    def journal(self, job_id: str) -> EventJournal | None:
        return self.journals.get(job_id)

    def _verify(self, pr: dict[str, Any], run_id: str) -> None:
        n = pr["number"]
        head, base = f"refs/proofmerge/pr-{n}", f"refs/proofmerge/base-{n}"
        job = self.job
        journal = self.journals.get(run_id) or EventJournal(run_id, run_id)

        def finish(status: str, step: str, **fields: Any) -> None:
            job.update(status=status, step=step, **fields)
            data = {"exit_code": fields.get("exit_code")}
            if status == "done":
                journal.append("job.completed", status="done", message=step, data=data)
            else:
                journal.append("job.failed", status="failed", message=step, data=data)

        try:
            journal.append("job.fetch_started", status="running",
                           message="Fetching the PR and its base into refs/proofmerge/")
            # gh's sign-in also lets git fetch private repositories, without touching git config.
            fetched = self._step(["git", "-C", str(self.repo), *GH_CREDENTIALS, "fetch", "--no-tags",
                                  f"{self._repository()[1]}.git",
                                  f"+refs/pull/{n}/head:{head}", f"+refs/heads/{pr['base']}:{base}"], 300)
            if fetched.returncode != 0:
                finish("failed", "git fetch failed", exit_code=fetched.returncode)
                return
            journal.append("job.fetch_completed", status="running", message="Fetched; starting proofmerge verify")
            job["step"] = "Running proofmerge verify (a few minutes)"
            with tempfile.TemporaryDirectory(prefix="proofmerge-pr-") as tmp:
                intent = Path(tmp) / "intent.md"
                intent.write_text(f"{pr['title']}\n\n{pr['body']}", encoding="utf-8")
                events = Path(tmp) / "events.jsonl"  # outside the evidence dir: not evidence
                stop = threading.Event()
                tailer = threading.Thread(target=tail_events, args=(events, journal, stop), daemon=True)
                tailer.start()
                try:
                    done = self._step([
                        sys.executable, "-m", "proofmerge.cli", "verify", str(self.repo),
                        "--base", base, "--head", head, "--intent-file", str(intent),
                        "--evidence-dir", str(self.root / run_id), "--events-file", str(events),
                    ], VERIFY_TIMEOUT, {**self._environment(), "PROOFMERGE_JOB_ID": run_id})
                finally:
                    stop.set()
                    tailer.join(10)
            # verify exits 0 for PASS/REVIEW and 2 for BLOCK; 1 means it could not run.
            # Exit 0 without a proof means there was nothing to check.
            ok = done.returncode in (0, 2) and (self.root / run_id / "proof.json").is_file()
            finish("done" if ok else "failed", "Finished" if ok else "verify did not produce a proof",
                   exit_code=done.returncode, output=((done.stdout or "") + (done.stderr or ""))[-4000:])
        except subprocess.TimeoutExpired:
            try:
                self._kill_containers(run_id)
                step = "Timed out; its containers were stopped"
            except (OSError, subprocess.SubprocessError):
                step = "Timed out; its containers may still be running (check `docker ps`)"
            finish("failed", step)
        except OSError as error:
            finish("failed", f"Could not start: {type(error).__name__}")
        except PrError as error:
            finish("failed", str(error))
