"""`proofmerge dashboard`: a local view of every evidence run under one folder.

The server binds to 127.0.0.1. The browser app (/app) can start CLI actions;
with a project selected, both APIs can start PR verification. Every POST and GET
that reveals local folders or job output needs the per-server session token; POSTs need
a JSON body and a same-origin Origin header, and every request must name
this server in its Host header, so another website or a DNS-rebinding page cannot
read data or start a run.

Two APIs share these rules: the original /api/* used by the legacy page (/legacy),
and the typed /api/v1/* (dashboard_api.py) used by the Control Center, including a
server-sent event stream of a running verification.

Clients name a run by an id from the latest scan; ids are looked up, never joined into
a path. All evidence text is untrusted and is rendered as text by the page.
"""

import json
import mimetypes
import secrets
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import BaseModel, ValidationError

from proofmerge.dashboard_events import sse_stream
from proofmerge.dashboard_github import GitHub, GitHubError
from proofmerge.dashboard_jobs import JobError, Jobs, branches, browse, git_project
from proofmerge.dashboard_models import ApiError, VerificationRequest
from proofmerge.dashboard_prs import PrError, PullRequests
from proofmerge.evidence import Proof
from proofmerge.fixes import load_report
from proofmerge.manifest import verify_manifest
from proofmerge.ponytail import load_report as load_ponytail
from proofmerge.rederive import rederive
from proofmerge.report import MAX_FAILURE_LINES, _read_evidence

STATIC = Path(__file__).parent / "dashboard_static"
MAX_RUNS = 2000
MAX_LOG = 6000  # characters kept from the end of each execution's stdout and stderr
# The id of a run whose proof.json sits in the dashboard folder itself. Not ".", which
# URLs normalise away (/runs/. is /runs/). A subfolder with this exact name wins.
ROOT_RUN = "(root)"
MAX_BODY = 64_000
MAX_STREAMS = 8  # concurrent server-sent event streams; each holds a thread
# Control Center routes: the server answers them with the app shell, whose router
# reads the path. /legacy keeps the previous dashboard reachable during the migration.
GITHUB_ACTIONS = {"/api/github/login": "start_login", "/api/github/login/cancel": "cancel_login",
                  "/api/github/logout": "logout", "/api/github/clone": "start_clone",
                  "/api/github/add": "add_account", "/api/github/switch": "switch_account"}
APP_ROUTES = ("/pull-requests","/verifications", "/runs", "/benchmarks", "/diagnostics")
_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
# report.html is self-contained: an inline <style> and no scripts, so it needs no 'self'.
_REPORT_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
               "frame-ancestors 'none'; base-uri 'none'")


class _Cache:
    """Parsed proofs and integrity results, reused while the files are unchanged.

    A proof is keyed by its proof.json (mtime, size). An integrity result is keyed by
    the (path, size, mtime) of every file in the run folder, so editing, adding or
    removing any evidence file re-hashes the run. Like the manifest itself, this
    detects accidental or naive changes; it is not a signature.
    """

    def __init__(self, limit: int = MAX_RUNS) -> None:
        self._proofs: dict[Path, tuple[tuple[int, int], Proof]] = {}
        self._integrity: dict[Path, tuple[tuple, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._limit = limit

    def _trim(self, table: dict) -> None:
        while len(table) > self._limit:
            table.pop(next(iter(table)))

    def proof(self, path: Path) -> Proof:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            hit = self._proofs.get(path)
        if hit and hit[0] == key:
            return hit[1]
        proof = Proof.model_validate_json(path.read_text())
        with self._lock:
            self._proofs[path] = (key, proof)
            self._trim(self._proofs)
        return proof

    def integrity(self, run_dir: Path, proof: Proof | None = None) -> dict[str, Any]:
        # proof.json's own stat is in the key, so a rewritten proof is re-checked.
        key = (proof is not None,) + tuple(
            _signature(run_dir, p) for p in sorted(run_dir.rglob("*"))
            if p.is_file() or p.is_symlink())
        with self._lock:
            hit = self._integrity.get(run_dir)
        if hit and hit[0] == key:
            return hit[1]
        result = _check_integrity(run_dir, proof)
        with self._lock:
            self._integrity[run_dir] = (key, result)
            self._trim(self._integrity)
        return result


CACHE = _Cache()


def _signature(run_dir: Path, path: Path) -> tuple[str, int, int]:
    stat = path.lstat()
    return path.relative_to(run_dir).as_posix(), stat.st_size, stat.st_mtime_ns


def _check_integrity(run_dir: Path, proof: Proof | None = None) -> dict[str, Any]:
    if not (run_dir / "manifest.json").is_file():
        return {"status": "unsealed", "problems": ["No manifest.json in this run."]}
    problems = verify_manifest(run_dir)
    # A rewritten proof can come with a rewritten manifest; the verdict must still follow.
    if proof is not None:
        problems += rederive(proof) or []
    return {"status": "changed" if problems else "intact", "problems": problems}


def _integrity(run_dir: Path, proof: Proof | None = None) -> dict[str, Any]:
    return CACHE.integrity(run_dir, proof)


def _label(run_dir: Path) -> dict[str, Any] | None:
    """The benchmark label for a run, from a run_bench results.json next to its folder."""
    results = run_dir.parent / "results.json"
    try:
        entry = json.loads(results.read_text())["candidates"][run_dir.name]
        return {"label": entry["label"], "held_out": bool(entry.get("held_out", False))}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def scan(root: Path) -> dict[str, Path]:
    """Run id (folder relative to root) -> run folder, for every proof.json under root."""
    root = root.resolve()
    runs = {}
    for proof in sorted(root.rglob("proof.json"))[:MAX_RUNS]:
        run_dir = proof.parent.resolve()
        # rglob does not follow directory symlinks, but a symlinked proof.json could point out.
        if proof.is_file() and not proof.is_symlink() and run_dir.is_relative_to(root):
            relative = run_dir.relative_to(root).as_posix()
            runs[relative] = run_dir
    if "." in runs:
        runs.setdefault(ROOT_RUN, runs["."])
        del runs["."]
    return runs


def _disagrees(decision: str | None, benchmark: dict[str, Any] | None) -> bool:
    """Whether a benchmark label and the recorded decision differ (a comparison only)."""
    if not benchmark or not decision:
        return False
    return decision != "BLOCK" if benchmark["label"] == "BLOCK" else decision == "BLOCK"


def _summary(run_id: str, run_dir: Path) -> dict[str, Any]:
    try:
        proof = CACHE.proof(run_dir / "proof.json")
        modified = (run_dir / "proof.json").stat().st_mtime
    except (OSError, ValueError) as error:
        return {"id": run_id, "error": f"proof.json could not be read: {type(error).__name__}"}
    usage = proof.model_usage
    benchmark = _label(run_dir)
    return {
        "id": run_id,
        "run_id": proof.run_id,
        "proof_path": str(run_dir / "proof.json"),
        "decision": proof.verdict.decision,
        "reasons": proof.verdict.reasons,
        "base_sha": proof.base_sha,
        "head_sha": proof.head_sha,
        "hypotheses": len(proof.hypotheses.hypotheses),
        "regressions": sum(f.verified for f in proof.findings),
        "tamper": len(proof.tamper_findings),
        "calls": usage.calls,
        "tokens": usage.prompt_tokens + usage.completion_tokens,
        "endpoint": getattr(usage, "endpoint", None),
        "models": getattr(usage, "models", {}),
        "integrity": _integrity(run_dir, proof),
        "benchmark": benchmark,
        "label_disagrees": _disagrees(proof.verdict.decision, benchmark),
        "orchestrator": proof.orchestration.engine if proof.orchestration else None,
        "modified": modified,
        "has_report": (run_dir / "report.html").is_file(),
    }


def _benchmarks(root: Path) -> list[dict[str, Any]]:
    """Every run_bench or run_baselines results.json under root, trimmed to what is shown."""
    found = []
    for path in sorted(root.resolve().rglob("results.json"))[:200]:
        try:
            data = json.loads(path.read_text())
            candidates = data["candidates"]
            kind = "baselines" if any("baseline_a" in c for c in candidates.values()) else "run"
            rows = []
            for name, c in candidates.items():
                row = {"name": name, "label": c["label"], "held_out": bool(c.get("held_out"))}
                if kind == "run":
                    row |= {"decision": c["decision"], "reasons": c.get("reasons", []),
                            "calls": c.get("model_calls", 0)}
                else:
                    row |= {"a": (c.get("baseline_a") or {}).get("decision"),
                            "b": (c.get("baseline_b") or {}).get("decision"),
                            "b_reason": (c.get("baseline_b") or {}).get("reason")}
                rows.append(row)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        found.append({
            "id": ROOT_RUN if (rel := path.parent.resolve().relative_to(root.resolve()).as_posix()) == "."
            else rel,
            # Candidate runs live next to results.json: "<id>/<name>", or "<name>" at the root.
            "run_prefix": "" if rel == "." else f"{rel}/",
            "kind": kind, "git_sha": meta.get("git_sha"), "date": meta.get("date"),
            "model": meta.get("spark_model"), "candidates": rows,
        })
    return found


def _runs_by_head(runs: dict[str, Path]) -> dict[str, list[str]]:
    by_head: dict[str, list[str]] = {}
    for run_id, run_dir in runs.items():
        try:
            head = json.loads((run_dir / "proof.json").read_text())["head_sha"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        by_head.setdefault(str(head), []).append(run_id)
    return by_head


def _detail(run_id: str, run_dir: Path) -> dict[str, Any]:
    proof = CACHE.proof(run_dir / "proof.json")
    probes, failures, logs = {}, {}, {}
    for experiment in proof.experiments:
        for run in experiment.executions:
            logs[f"{experiment.hypothesis_id}:{run.revision}:{run.attempt}"] = {
                "outcome": run.outcome, "exit_code": run.exit_code, "duration_ms": run.duration_ms,
                "command": run.command_argv,
                "stdout": (_read_evidence(run_dir, run.stdout_path) or "")[-MAX_LOG:],
                "stderr": (_read_evidence(run_dir, run.stderr_path) or "")[-MAX_LOG:],
            }
        probes[experiment.hypothesis_id] = _read_evidence(run_dir, experiment.test_source_path)
        failed = next((r for r in experiment.executions
                       if r.revision == "head" and r.outcome == "FAIL"), None)
        log = None if failed is None else _read_evidence(run_dir, failed.stdout_path)
        lines = [] if log is None else [x for x in log.splitlines() if x.startswith("E ")]
        failures[experiment.hypothesis_id] = lines[:MAX_FAILURE_LINES]
    return {
        "summary": _summary(run_id, run_dir),
        "proof": proof.model_dump(mode="json"),
        "probes": probes,
        "failures": failures,
        "logs": logs,
        "fix": None if (report := load_report(run_dir)) is None else report.model_dump(mode="json"),
        "ponytail": None if (pony := load_ponytail(run_dir)) is None else pony.model_dump(mode="json"),
    }


class _Handler(BaseHTTPRequestHandler):
    root: Path
    prs: PullRequests | None
    jobs: Jobs
    pinned_repo: bool
    action_lock: Any
    token: str
    runner: Any
    streams: threading.BoundedSemaphore
    heartbeat: float

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str, csp: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (_HEADERS | ({"Content-Security-Policy": csp} if csp else {})).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _model(self, model: BaseModel, status: int = HTTPStatus.OK) -> None:
        self._send(status, model.model_dump_json().encode(), "application/json")

    def _origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        # A DNS-rebinding page reaches 127.0.0.1 under its own host name; refuse it.
        if self.headers.get("Host") in self._origins():
            return True
        self._json({"error": "unexpected Host header"}, HTTPStatus.MISDIRECTED_REQUEST)
        return False

    def _report(self, run_dir: Path) -> None:
        report = run_dir / "report.html"
        if report.is_file() and not report.is_symlink():
            self._send(HTTPStatus.OK, report.read_bytes(), "text/html; charset=utf-8", _REPORT_CSP)
        else:
            self._json({"error": "no report"}, HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        if not self._host_ok():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path in ("/api/health", "/api/browse", "/api/branches", "/api/job",
                        "/api/github", "/api/github/repos"):
            if not secrets.compare_digest(self.headers.get("X-ProofMerge-Token", ""), self.token):
                self._json({"error": "refused: open the dashboard page and try again"},
                           HTTPStatus.FORBIDDEN)
                return
            try:
                if url.path == "/api/github":
                    self._json(self.github.status())
                elif url.path == "/api/github/repos":
                    self._json({"repos": self.github.repos()})
                elif url.path == "/api/health":
                    self._json(self.jobs.health())
                elif url.path == "/api/browse":
                    self._json(browse(query.get("path", [""])[0]))
                elif url.path == "/api/branches":
                    project = git_project(query.get("project", [""])[0], self.runner)
                    self._json(branches(project, self.runner))
                else:
                    self._json({"job": self.jobs.progress()})
            except (JobError, GitHubError) as error:
                self._json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        elif url.path.startswith("/api/v1/"):
            self._v1_get(url.path.removeprefix("/api/v1"), query)
        elif url.path == "/api/ids":
            # Cheap check for new runs: a scan without hashing or parsing.
            self._json(sorted(scan(self.root)))
        elif url.path == "/api/session":
            self._json({"token": self.token, "prs": self.prs is not None,
                        "settings": self.jobs.settings()})
        elif url.path == "/api/prs":
            if self.prs is None:
                self._json({"error": "start the dashboard with --repo to list pull requests"},
                           HTTPStatus.NOT_FOUND)
                return
            try:
                prs = self.prs.pulls(refresh=query.get("refresh") == ["1"])
            except PrError as error:
                self._json({"error": str(error), "job": self.prs.job}, HTTPStatus.BAD_GATEWAY)
                return
            runs = scan(self.root)
            by_head = _runs_by_head(runs)

            def older(pr: dict[str, Any]) -> list[str]:
                # Runs started from this page are named pr-<n>-<sha>-<time>.
                current = set(by_head.get(pr["head_sha"], []))
                prefix = f"pr-{pr['number']}-"
                return [i for i in runs if i.rsplit("/", 1)[-1].startswith(prefix) and i not in current]

            self._json({"prs": [p | {"runs": by_head.get(p["head_sha"], []), "older_runs": older(p),
                                     "ponytail": self.prs.ponytail_report(p)}
                                for p in prs], "job": self.prs.job, "ponytail_job": self.prs.pony})
        elif url.path == "/api/index":
            runs = scan(self.root)
            self._json({"root": self.root.name, "runs": [_summary(k, v) for k, v in runs.items()],
                        "benchmarks": _benchmarks(self.root)})
        elif url.path in ("/api/run", "/report"):
            run_dir = scan(self.root).get(query.get("id", [""])[0])
            if run_dir is None:
                self._json({"error": "unknown run"}, HTTPStatus.NOT_FOUND)
            elif url.path == "/report":
                self._report(run_dir)
            else:
                try:
                    self._json(_detail(query["id"][0], run_dir))
                except (OSError, ValueError):
                    self._json({"error": "proof.json could not be read"}, HTTPStatus.UNPROCESSABLE_ENTITY)
        else:
            self._static(url.path)

    def _static(self, path: str) -> None:
        """Static files are looked up by name in a fixed table, never joined into a path."""
        files = {p.relative_to(STATIC).as_posix(): p for p in STATIC.rglob("*") if p.is_file()}
        app = "app/index.html" if "app/index.html" in files else "legacy/index.html"
        if path.startswith("/static/"):
            name = path.removeprefix("/static/")
        elif path in ("/legacy", "/legacy/", "/app", "/app/"):
            name = "legacy/index.html"
        elif path == "/" or any(path == r or path.startswith(r + "/") for r in APP_ROUTES):
            name = app  # a Control Center route; the app's router reads the path
        else:
            name = None
        target = files.get(name) if name else None
        if target is None:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), f"{content_type}; charset=utf-8")

    def _v1_get(self, path: str, query: dict[str, list[str]]) -> None:
        from proofmerge import dashboard_api as api  # imports this module; load on first use

        parts = [unquote(p) for p in path.strip("/").split("/")]
        if parts == ["session"]:
            self._model(api.session(self.token, self.prs))
        elif parts == ["overview"]:
            self._model(api.overview(self.root, self.prs))
        elif parts == ["runs"]:
            self._model(api.runs_page(self.root, query))
        elif parts[:1] == ["runs"] and len(parts) in (2, 3) and parts[2:] in ([], ["report"]):
            run_dir = scan(self.root).get(parts[1])  # an id from the scan, never a path
            if run_dir is None:
                self._model(ApiError(error="unknown run"), HTTPStatus.NOT_FOUND)
            elif parts[2:] == ["report"]:
                self._report(run_dir)
            else:
                try:
                    self._model(api.run_detail(parts[1], run_dir))
                except (OSError, ValueError):
                    self._model(ApiError(error="proof.json could not be read"),
                                HTTPStatus.UNPROCESSABLE_ENTITY)
        elif parts == ["benchmarks"]:
            self._model(api.benchmarks(self.root))
        elif parts == ["pull-requests"]:
            if self.prs is None:
                self._model(ApiError(error="start the dashboard with --repo to list pull requests"),
                            HTTPStatus.NOT_FOUND)
                return
            try:
                self._model(api.pull_requests(self.root, self.prs, _one(query, "refresh") == "1"))
            except PrError as error:
                self._model(ApiError(error=str(error)), HTTPStatus.BAD_GATEWAY)
        elif parts == ["jobs", "active"]:
            job = api.job_model(self.prs)
            self._json(None if job is None else job.model_dump(mode="json"))
        elif parts[:1] == ["jobs"] and len(parts) in (2, 3) and parts[2:] in ([], ["events"]):
            last = _last_event_id(self.headers.get("Last-Event-ID"), query)
            found = api.job_events(self.prs, parts[1], last)
            if found is None:
                self._model(ApiError(error="unknown job"), HTTPStatus.NOT_FOUND)
            elif parts[2:] == ["events"]:
                self._sse(parts[1], last)
            else:
                self._model(found)
        elif parts == ["diagnostics"]:
            self._model(api.diagnostics(self.root, self.prs, self.runner))
        elif parts == ["schema", "proof"]:
            self._json(Proof.model_json_schema())
        else:
            self._model(ApiError(error="not found"), HTTPStatus.NOT_FOUND)

    def _sse(self, job_id: str, last_id: int) -> None:
        """Stream a job's events. Bounded: few streams, an hour at most, ends with the job."""
        journal = self.prs.journal(job_id) if self.prs else None
        if journal is None:
            self._model(ApiError(error="unknown job"), HTTPStatus.NOT_FOUND)
            return
        if not self.streams.acquire(blocking=False):
            self._model(ApiError(error="too many open event streams"), HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            for name, value in _HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            for frame in sse_stream(journal, last_id, heartbeat=self.heartbeat):
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # the browser went away; EventSource reconnects with Last-Event-ID
        finally:
            self.streams.release()
            self.close_connection = True

    def _post_allowed(self) -> bool:
        origin = urlsplit(self.headers.get("Origin", ""))
        if (origin.scheme != "http" or origin.netloc not in self._origins()
                or self.headers.get("Content-Type") != "application/json"
                or not secrets.compare_digest(self.headers.get("X-ProofMerge-Token", ""), self.token)):
            self._json({"error": "refused: open the dashboard page and try again"},
                       HTTPStatus.FORBIDDEN)
            return False
        return True

    def do_POST(self) -> None:
        with self.action_lock:
            self._post()

    def _post(self) -> None:
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        routes = ("/api/v1/verifications", "/api/verify", "/api/ponytail", "/api/jobs", "/api/jobs/stop", "/api/settings", "/api/quit",
                  *GITHUB_ACTIONS)
        if path not in routes or (path in ("/api/verify", "/api/ponytail", "/api/v1/verifications") and self.prs is None):
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._post_allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (1024 if path in ("/api/verify", "/api/ponytail", "/api/v1/verifications") else MAX_BODY):
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise TypeError
        except (ValueError, TypeError):
            self._json({"error": "send a JSON object"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/ponytail":
            try:
                number = VerificationRequest.model_validate(body).number
            except ValidationError:
                self._json({"error": "send {\"number\": <PR number>}"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                self._json(self.prs.start_ponytail(number), HTTPStatus.ACCEPTED)
            except PrError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if path in ("/api/verify", "/api/v1/verifications"):
            try:
                number = VerificationRequest.model_validate(body).number
            except ValidationError:
                self._json({"error": "send {\"number\": <PR number>}"}, HTTPStatus.BAD_REQUEST)
                return
            if self.jobs.job and self.jobs.job["status"] == "running":
                self._json({"error": "Another action is still running."}, HTTPStatus.CONFLICT)
                return
            try:
                job = self.prs.start(number)
                if path == "/api/verify":
                    self._json(job, HTTPStatus.ACCEPTED)
                else:
                    from proofmerge import dashboard_api as api
                    self._model(api.job_model(self.prs), HTTPStatus.ACCEPTED)
            except PrError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if path in GITHUB_ACTIONS:
            try:
                if path == "/api/github/clone":
                    self.github.start_clone(body.get("repo"))
                elif path == "/api/github/switch":
                    if (self.jobs.job and self.jobs.job["status"] == "running") or (
                            self.prs and any(j and j["status"] == "running" for j in (self.prs.job, self.prs.pony))):
                        raise GitHubError("Wait for the running check to finish before switching accounts.")
                    self.github.switch_account(body.get("user"))
                    if self.prs:
                        self.prs.forget()  # another account may see other repositories and pull requests
                else:
                    getattr(self.github, GITHUB_ACTIONS[path])()
            except GitHubError as error:
                self._json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            started = path in ("/api/github/login", "/api/github/add", "/api/github/clone")
            self._json(self.github.status(), HTTPStatus.ACCEPTED if started else HTTPStatus.OK)
            return
        try:
            if path == "/api/jobs":
                if self.prs and self.prs.job and self.prs.job["status"] == "running":
                    raise JobError("A pull request is still being verified.")
                self.jobs.start(body, scan(self.root))
                self._json({"job": self.jobs.progress()}, HTTPStatus.ACCEPTED)
            elif path == "/api/jobs/stop":
                self.jobs.cancel()
                self._json({"job": self.jobs.progress()})
            elif path == "/api/settings":
                if "key" in body:
                    self.jobs.set_key(body["key"])
                if "model" in body:
                    self.jobs.set_model(body["model"])
                if "project" in body:
                    if self.prs and self.prs.job and self.prs.job["status"] == "running":
                        raise JobError("A pull request is still being verified.")
                    project = git_project(str(body["project"]), self.runner)
                    self.jobs.project = str(project)
                    # Pull requests follow the chosen project unless --repo pinned one.
                    if not self.pinned_repo:
                        type(self).prs = PullRequests(project, self.root, self.runner, self.jobs.environment)
                self._json({"settings": self.jobs.settings(), "prs": self.prs is not None})
            else:  # /api/quit: the app has no terminal to press Ctrl+C in
                self._json({"bye": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except JobError as error:
            self._json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)

    def _refuse(self) -> None:
        self._json({"error": "not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_DELETE = do_PATCH = _refuse


def _one(query: dict[str, list[str]], name: str) -> str:
    return (query.get(name) or [""])[0]


def _last_event_id(header: str | None, query: dict[str, list[str]]) -> int:
    """EventSource sends Last-Event-ID when it reconnects; a query value works too."""
    for value in (header, _one(query, "last_event_id")):
        if value and value.isdigit() and len(value) < 10:
            return int(value)
    return 0


def make_server(root: Path, port: int, repo: Path | None = None,
                runner: Any = subprocess.run, *, heartbeat: float = 15.0,
                github: GitHub | None = None, settings_file: Path | None = None) -> ThreadingHTTPServer:
    jobs = Jobs(root, runner, settings_file=settings_file)
    github = github or GitHub(Path.home() / "ProofMerge" / "repos")
    prs = PullRequests(repo, root, runner, jobs.environment) if repo is not None else None
    if repo is not None:
        jobs.project = str(repo.resolve())
    handler = type("Handler", (_Handler,), {
        "root": root.resolve(), "prs": prs, "jobs": jobs, "github": github,
        "runner": staticmethod(runner),
        "action_lock": threading.Lock(), "pinned_repo": repo is not None,
        "token": secrets.token_urlsafe(32),
        "streams": threading.BoundedSemaphore(MAX_STREAMS), "heartbeat": heartbeat,
    })
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
