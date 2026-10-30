"""`proofmerge dashboard`: a local view of every evidence run under one folder.

The server binds to 127.0.0.1 and only reads files, with one exception: with `--repo`,
POST /api/verify starts `proofmerge verify` on a pull request (dashboard_prs.py). That
request needs the per-server token from /api/session, a JSON body and a same-origin
Origin header, and every request must name this server in its Host header, so another
website or a DNS-rebinding page cannot read data or start a run.

Clients name a run by an id from the latest scan; ids are looked up, never joined into
a path. All evidence text is untrusted and is rendered as text by the page.
"""

import json
import mimetypes
import secrets
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from proofmerge.dashboard_prs import PrError, PullRequests
from proofmerge.evidence import Proof
from proofmerge.manifest import verify_manifest
from proofmerge.report import MAX_FAILURE_LINES, _read_evidence

STATIC = Path(__file__).parent / "dashboard_static"
MAX_RUNS = 2000
MAX_LOG = 6000  # characters kept from the end of each execution's stdout and stderr
_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _integrity(run_dir: Path) -> dict[str, Any]:
    if not (run_dir / "manifest.json").is_file():
        return {"status": "unsealed", "problems": ["No manifest.json in this run."]}
    problems = verify_manifest(run_dir)
    return {"status": "changed" if problems else "intact", "problems": problems}


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
            runs[run_dir.relative_to(root).as_posix() or "."] = run_dir
    return runs


def _summary(run_id: str, run_dir: Path) -> dict[str, Any]:
    try:
        proof = Proof.model_validate_json((run_dir / "proof.json").read_text())
    except (OSError, ValueError) as error:
        return {"id": run_id, "error": f"proof.json could not be read: {type(error).__name__}"}
    usage = proof.model_usage
    return {
        "id": run_id,
        "run_id": proof.run_id,
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
        "integrity": _integrity(run_dir),
        "benchmark": _label(run_dir),
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
            "id": path.parent.resolve().relative_to(root.resolve()).as_posix() or ".",
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
    proof = Proof.model_validate_json((run_dir / "proof.json").read_text())
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
    }


class _Handler(BaseHTTPRequestHandler):
    root: Path
    prs: PullRequests | None
    token: str

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        # A DNS-rebinding page reaches 127.0.0.1 under its own host name; refuse it.
        if self.headers.get("Host") in self._origins():
            return True
        self._json({"error": "unexpected Host header"}, HTTPStatus.MISDIRECTED_REQUEST)
        return False

    def do_GET(self) -> None:
        if not self._host_ok():
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/ids":
            # Cheap check for new runs: a scan without hashing or parsing.
            self._json(sorted(scan(self.root)))
        elif url.path == "/api/session":
            self._json({"token": self.token, "prs": self.prs is not None})
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

            self._json({"prs": [p | {"runs": by_head.get(p["head_sha"], []), "older_runs": older(p)}
                                for p in prs], "job": self.prs.job})
        elif url.path == "/api/index":
            runs = scan(self.root)
            self._json({"root": self.root.name, "runs": [_summary(k, v) for k, v in runs.items()],
                        "benchmarks": _benchmarks(self.root)})
        elif url.path in ("/api/run", "/report"):
            run_dir = scan(self.root).get(query.get("id", [""])[0])
            if run_dir is None:
                self._json({"error": "unknown run"}, HTTPStatus.NOT_FOUND)
            elif url.path == "/report":
                report = run_dir / "report.html"
                if report.is_file() and not report.is_symlink():
                    self._send(HTTPStatus.OK, report.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json({"error": "no report"}, HTTPStatus.NOT_FOUND)
            else:
                try:
                    self._json(_detail(query["id"][0], run_dir))
                except (OSError, ValueError):
                    self._json({"error": "proof.json could not be read"}, HTTPStatus.UNPROCESSABLE_ENTITY)
        else:
            name = "index.html" if url.path == "/" else url.path.removeprefix("/static/")
            files = {p.relative_to(STATIC).as_posix(): p for p in STATIC.rglob("*") if p.is_file()}
            path = files.get(name)  # looked up, never joined
            if path is None or (url.path != "/" and not url.path.startswith("/static/")):
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._send(HTTPStatus.OK, path.read_bytes(), f"{content_type}; charset=utf-8")

    def do_POST(self) -> None:
        if not self._host_ok():
            return
        if urlsplit(self.path).path != "/api/verify" or self.prs is None:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        origin = urlsplit(self.headers.get("Origin", ""))
        if (origin.scheme != "http" or origin.netloc not in self._origins()
                or self.headers.get("Content-Type") != "application/json"
                or not secrets.compare_digest(self.headers.get("X-ProofMerge-Token", ""), self.token)):
            self._json({"error": "refused: open the dashboard page and try again"},
                       HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise ValueError
            number = json.loads(self.rfile.read(length))["number"]
            if type(number) is not int or number <= 0:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self._json({"error": "send {\"number\": <PR number>}"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            self._json(self.prs.start(number), HTTPStatus.ACCEPTED)
        except PrError as error:
            self._json({"error": str(error)}, HTTPStatus.CONFLICT)

    def _refuse(self) -> None:
        self._json({"error": "not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_DELETE = do_PATCH = _refuse


def make_server(root: Path, port: int, repo: Path | None = None,
                runner: Any = subprocess.run) -> ThreadingHTTPServer:
    prs = PullRequests(repo, root, runner) if repo is not None else None
    handler = type("Handler", (_Handler,), {
        "root": root.resolve(), "prs": prs, "token": secrets.token_urlsafe(32),
    })
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
