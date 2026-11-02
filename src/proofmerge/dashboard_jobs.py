"""Every CLI command, started from the dashboard page instead of a terminal.

A job runs `python -m proofmerge.cli <command> ...` as a child process: an argument list,
never a shell, so the code under test still only runs inside the Docker sandbox. One job
runs at a time. Inputs from the page are checked here before they reach git or the CLI:
a project must be a Git work tree, refs must resolve to commits, and runs are named by
the server, so a client never picks a path to write.

The model key typed into the page lives only in this process's memory and in the
environment of the jobs it starts; it is never written to disk or sent back to the page.
"""

import os
import re
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proofmerge import __version__

MAX_OUTPUT = 20_000  # characters of job output kept for the page
MAX_INTENT = 20_000
MAX_DIRS = 400
JOB_TIMEOUT = 1800
CHECK_TIMEOUT = 15
COMMANDS = ("verify", "probe", "hypotheses", "replay", "rerun", "smoke", "setup")
REF = re.compile(r"[A-Za-z0-9._/@{}^~-]{1,200}")
KEY = re.compile(r"[\x21-\x7e]{8,512}")
Spawn = Callable[..., Any]
Runner = Callable[..., Any]


class JobError(Exception):
    """A failure safe to show in the page."""


def _which(runner: Runner, argv: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return runner(argv, capture_output=True, text=True, timeout=CHECK_TIMEOUT, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def git_project(path: str, runner: Runner = subprocess.run) -> Path:
    """The folder as a Git work tree, or JobError."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise JobError("Choose a project folder.")
    folder = Path(path.strip()).expanduser()
    if not folder.is_absolute() or not folder.is_dir():
        raise JobError(f"{folder} is not a folder on this computer.")
    folder = folder.resolve()
    done = _which(runner, ["git", "-C", str(folder), "rev-parse", "--show-toplevel"])
    if done is None:
        raise JobError("Git is not installed. Install Git, then try again.")
    if done.returncode != 0:
        raise JobError(f"{folder.name} is not a Git project. Pick the folder that holds .git.")
    return Path(done.stdout.strip()).resolve()


def check_ref(project: Path, ref: str, runner: Runner = subprocess.run) -> str:
    """A ref that names a commit in the project; never one that git could read as an option."""
    if not isinstance(ref, str) or not REF.fullmatch(ref) or ref.startswith("-") or ".." in ref:
        raise JobError(f"{ref!r} is not a branch, tag or commit name.")
    done = _which(runner, ["git", "-C", str(project), "rev-parse", "--verify", "--quiet",
                           "--end-of-options", f"{ref}^{{commit}}"])
    if done is None or done.returncode != 0:
        raise JobError(f"{ref} is not a branch or commit in {project.name}.")
    return ref


def branches(project: Path, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Local and remote branches, newest first, and a guess at the base and the head."""
    done = _which(runner, ["git", "-C", str(project), "for-each-ref", "--sort=-committerdate",
                           "--format=%(refname:short)\t%(committerdate:relative)\t%(subject)",
                           "refs/heads", "refs/remotes"])
    if done is None or done.returncode != 0:
        raise JobError("Could not list the branches of this project.")
    found = []
    for line in done.stdout.splitlines()[:300]:
        name, _, rest = line.partition("\t")
        when, _, subject = rest.partition("\t")
        if name and not name.endswith("/HEAD") and REF.fullmatch(name):
            found.append({"name": name, "when": when, "subject": subject[:160]})
    names = [b["name"] for b in found]
    current = _which(runner, ["git", "-C", str(project), "branch", "--show-current"])
    head = current.stdout.strip() if current is not None and current.returncode == 0 else ""
    base = next((n for n in ("main", "master", "origin/main", "origin/master") if n in names), "")
    if head == base or head not in names:
        head = next((n for n in names if n != base), "")
    return {"project": str(project), "name": project.name, "branches": found,
            "base": base, "head": head}


def browse(path: str) -> dict[str, Any]:
    """Sub-folders of a folder, for the folder picker; hidden folders are left out."""
    if path:
        folder = Path(path).expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            raise JobError(f"{path} is not a folder on this computer.")
    else:
        folder = Path.home()
    folder = folder.resolve()
    dirs = []
    try:
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if len(dirs) >= MAX_DIRS:
                break
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            dirs.append({"name": entry.name, "path": str(entry), "git": (entry / ".git").exists()})
    except OSError:
        raise JobError(f"{folder} cannot be opened.") from None
    places = [{"name": "Home", "path": str(Path.home())}]
    if os.name == "nt":  # drive letters, so a Windows user can leave the home folder
        places += [{"name": f"{d}:", "path": f"{d}:\\"} for d in string.ascii_uppercase
                   if Path(f"{d}:\\").exists()]
    else:
        places.append({"name": "Computer", "path": "/"})
    return {"path": str(folder), "parent": str(folder.parent) if folder.parent != folder else None,
            "git": (folder / ".git").exists(), "dirs": dirs, "places": places}


class Jobs:
    def __init__(self, root: Path, runner: Runner = subprocess.run,
                 spawn: Spawn = subprocess.Popen) -> None:
        self.root = root.resolve()
        self._run = runner
        self._spawn = spawn
        self._lock = threading.Lock()
        self._process: Any = None
        self._key = ""  # MODEL_API_KEY typed into the page; memory only
        self.project = ""
        self.job: dict[str, Any] | None = None

    # Settings ---------------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        return {"project": self.project, "key_set": bool(self._key),
                "key_in_env": bool(os.environ.get("MODEL_API_KEY")),
                "backend": os.environ.get("PROOFMERGE_BACKEND", "api")}

    def set_key(self, key: str) -> None:
        if key == "":
            self._key = ""
            return
        if not isinstance(key, str) or not KEY.fullmatch(key.strip()):
            raise JobError("That does not look like an API key.")
        self._key = key.strip()

    def environment(self) -> dict[str, str]:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        if self._key:
            env["MODEL_API_KEY"] = self._key
        return env

    def health(self) -> dict[str, Any]:
        git = _which(self._run, ["git", "--version"])
        docker = _which(self._run, ["docker", "info", "--format", "{{.ServerVersion}}"])
        image = None
        if docker is not None and docker.returncode == 0:
            from proofmerge.sandbox import RUNNER_IMAGE
            image = _which(self._run, ["docker", "image", "inspect", "--format", "{{.Id}}", RUNNER_IMAGE])
        gh = _which(self._run, ["gh", "--version"])
        return {
            "version": __version__,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "git": git is not None and git.returncode == 0,
            "docker_installed": docker is not None,
            "docker_running": docker is not None and docker.returncode == 0,
            "runner_image": image is not None and image.returncode == 0,
            "gh": gh is not None and gh.returncode == 0,
            "model_key": bool(self._key or os.environ.get("MODEL_API_KEY"))
            or os.environ.get("PROOFMERGE_BACKEND", "api") != "api",
            "runs_folder": str(self.root),
        }

    # Jobs -------------------------------------------------------------------------------
    def _argv(self, request: dict[str, Any], runs: dict[str, Path]) -> tuple[list[str], str, str]:
        """(argv, run id or "", intent text) for a request from the page."""
        command = request.get("command")
        if command not in COMMANDS:
            raise JobError("Unknown action.")
        cli = [sys.executable, "-m", "proofmerge.cli"]
        if command in ("smoke", "setup"):
            return cli + [command], "", ""
        if command in ("replay", "rerun"):
            run_dir = runs.get(str(request.get("run", "")))
            if run_dir is None:
                raise JobError("That run is no longer in the runs folder.")
            argv = cli + ["replay", str(run_dir / "proof.json")]
            if command == "rerun":
                argv += ["--rerun", "--repo", str(git_project(str(request.get("project", "")), self._run))]
            return argv, "", ""
        project = git_project(str(request.get("project", "")), self._run)
        base = check_ref(project, request.get("base", ""), self._run)
        head = check_ref(project, request.get("head", ""), self._run)
        if base == head:
            raise JobError("Pick two different branches: the one to merge into and the change.")
        intent = request.get("intent") or ""
        if not isinstance(intent, str) or len(intent) > MAX_INTENT:
            raise JobError(f"Keep the description under {MAX_INTENT} characters.")
        argv = cli + [command, str(project), "--base", base, "--head", head]
        run_id = ""
        if command in ("verify", "probe"):
            slug = re.sub(r"[^A-Za-z0-9]+", "-", head.rsplit("/", 1)[-1]).strip("-")[:40] or "change"
            run_id = f"{time.strftime('%Y-%m-%d-%H%M%S')}-{slug}"
            argv += ["--evidence-dir", str(self.root / run_id)]
        self.project = str(project)
        return argv, run_id, intent

    def start(self, request: dict[str, Any], runs: dict[str, Path]) -> dict[str, Any]:
        with self._lock:
            if self.job and self.job["status"] == "running":
                raise JobError("Another action is still running. Wait for it or stop it.")
            argv, run_id, intent = self._argv(request, runs)
            self.job = {"id": uuid.uuid4().hex, "command": request["command"], "status": "running", "run": run_id,
                        "started": time.time(), "finished": None, "exit_code": None, "output": ""}
            job = self.job
        threading.Thread(target=self._work, args=(job, argv, intent), daemon=True).start()
        return job

    def _work(self, job: dict[str, Any], argv: list[str], intent: str) -> None:
        env = self.environment()
        env["PROOFMERGE_JOB_ID"] = job["id"]
        with tempfile.TemporaryDirectory(prefix="proofmerge-ui-") as tmp:
            if intent.strip():
                path = Path(tmp) / "intent.md"
                path.write_text(intent, encoding="utf-8")
                argv = argv + ["--intent-file", str(path)]
            try:
                process = self._spawn(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace", env=env)
            except OSError as error:
                job.update(status="failed", finished=time.time(),
                           output=f"Could not start: {type(error).__name__}")
                return
            self._process = process
            deadline = threading.Timer(JOB_TIMEOUT, self.cancel, kwargs={"reason": "timed out"})
            deadline.start()
            try:
                for line in process.stdout:
                    job["output"] = (job["output"] + line)[-MAX_OUTPUT:]
                code = process.wait()
            finally:
                deadline.cancel()
                self._process = None
        if job["status"] == "running":
            # verify exits 2 for BLOCK: the run worked and found a regression.
            ok = code == 0 or (code == 2 and job["command"] == "verify")
            if job["run"] and not (self.root / job["run"] / "proof.json").is_file():
                ok = ok and job["command"] != "verify"
            job.update(status="done" if ok else "failed")
        job.update(exit_code=code, finished=time.time())

    def cancel(self, reason: str = "stopped") -> dict[str, Any] | None:
        job, process = self.job, self._process
        if not job or job["status"] != "running" or process is None:
            raise JobError("Nothing is running.")
        job["status"] = reason
        process.kill()
        if job["id"]:  # killing the CLI leaves its sandbox containers; stop them by label
            listed = _which(self._run, ["docker", "ps", "-q", "--filter",
                                        f"label=proofmerge.job={job['id']}"])
            for container in (listed.stdout.split() if listed and listed.returncode == 0 else []):
                _which(self._run, ["docker", "kill", container])
        return job

    def progress(self) -> dict[str, Any] | None:
        """The job, plus which verify stage it reached, read from the files it has written."""
        job = self.job
        if job is None:
            return None
        # One flag per page step: read, hypotheses (drafts), sandbox runs, adjudication, verdict.
        seen = [False] * 5
        if job["run"]:
            run = self.root / job["run"]
            seen = [run.is_dir(), (run / "drafts").is_dir(), any(run.glob("H*/*.stdout")),
                    (run / "probes").is_dir(), (run / "proof.json").is_file()]
        stage = max((i + 1 for i, flag in enumerate(seen) if flag), default=0)
        return job | {"stage": stage, "seen": seen,
                      "elapsed": round((job["finished"] or time.time()) - job["started"])}
