"""The Control Center's /api/v1: typed responses, the same trust rules, and SSE progress."""

import json
import shutil
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_dashboard import DEMO, PRS, fetch, runs  # noqa: F401 - runs is a fixture

from proofmerge import dashboard_api
from proofmerge.dashboard import MAX_STREAMS, make_server
from proofmerge.dashboard_models import (
    Benchmarks,
    Diagnostics,
    JobEvents,
    OverviewMetrics,
    RunDetail,
    RunPage,
    Session,
)
from proofmerge.evidence import Proof
from proofmerge.pipeline import STAGES

STAGE_NAMES = [name for name, _ in STAGES]


def serve(root: Path, repo: Path | None = None, runner=None, heartbeat: float = 15.0):
    kwargs = {} if runner is None else {"runner": runner}
    httpd = make_server(root, 0, repo, heartbeat=heartbeat, **kwargs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


@pytest.fixture
def server(runs: Path):  # noqa: F811
    httpd, base = serve(runs)
    yield base
    httpd.shutdown()
    httpd.server_close()


def get(base: str, path: str, model=None, **headers):
    status, sent, body = fetch(base + path, headers=headers)
    data = json.loads(body) if sent.get("Content-Type", "").startswith("application/json") else body
    return status, (model.model_validate(data) if model and status == 200 else data)


def test_session_lists_the_pipeline_stages_in_order(server) -> None:
    status, session = get(server, "/api/v1/session", Session)
    assert status == 200 and session.api_version == 1 and session.prs is False
    assert session.stages == STAGE_NAMES
    assert set(session.model_stages) < set(STAGE_NAMES)


def test_runs_page_filters_counts_and_pages(server) -> None:
    status, page = get(server, "/api/v1/runs", RunPage)
    assert status == 200 and page.total == 2
    assert (page.counts.BLOCK, page.counts.REVIEW, page.counts.PASS) == (1, 1, 0)
    assert [r.id for r in page.runs] == ["tenant-cache-take2", "intended-change"]  # BLOCK first

    _, block = get(server, "/api/v1/runs?decision=BLOCK", RunPage)
    assert [r.id for r in block.runs] == ["tenant-cache-take2"]
    assert block.counts.REVIEW == 1  # counts ignore the decision filter, so the bar stays whole

    _, paged = get(server, "/api/v1/runs?limit=1&offset=1&sort=name", RunPage)
    assert (paged.total, [r.id for r in paged.runs]) == (2, ["tenant-cache-take2"])
    _, searched = get(server, "/api/v1/runs?q=tenant", RunPage)
    assert [r.id for r in searched.runs] == ["tenant-cache-take2"]
    _, odd = get(server, "/api/v1/runs?limit=abc&offset=-5&sort=nope", RunPage)
    assert (odd.limit, odd.offset, odd.total) == (50, 0, 2)


def test_unreadable_run_counts_as_error_not_a_decision(server, runs: Path) -> None:  # noqa: F811
    (runs / "broken").mkdir()
    (runs / "broken" / "proof.json").write_text("{")
    _, page = get(server, "/api/v1/runs?decision=ERROR", RunPage)
    assert [r.id for r in page.runs] == ["broken"] and page.runs[0].decision is None
    assert page.counts.unreadable == 1


def test_run_detail_verdict_is_exactly_the_proof_verdict(server, runs: Path) -> None:  # noqa: F811
    for run_id in ("tenant-cache-take2", "intended-change"):
        status, detail = get(server, f"/api/v1/runs/{run_id}", RunDetail)
        on_disk = Proof.model_validate_json((runs / run_id / "proof.json").read_text())
        assert status == 200
        assert detail.proof.verdict == on_disk.verdict
        assert detail.summary.decision == on_disk.verdict.decision
    assert detail.replay_command.startswith("proofmerge replay ")
    assert detail.replay_command.endswith("/intended-change/proof.json")


def test_run_detail_carries_probes_logs_and_failures(server) -> None:
    _, detail = get(server, "/api/v1/runs/tenant-cache-take2", RunDetail)
    assert "def test_" in detail.probes["H1"]
    assert any("DID NOT RAISE" in line for line in detail.failures["H1"])
    assert detail.logs["H1:head:1"].outcome == "FAIL"
    assert detail.summary.integrity.status == "intact"


def test_v1_run_ids_are_looked_up_never_joined(server) -> None:
    for path in ("/api/v1/runs/..%2F..%2Fetc", "/api/v1/runs/%2Fetc%2Fpasswd",
                 "/api/v1/runs/tenant-cache-take2%2F..", "/api/v1/runs/nope/report",
                 "/api/v1/runs/tenant-cache-take2/probes", "/api/v1/nope"):
        assert fetch(server + path)[0] == 404, path


def test_v1_report_keeps_the_script_free_csp(server) -> None:
    status, headers, body = fetch(server + "/api/v1/runs/tenant-cache-take2/report")
    assert status == 200 and b"BLOCK" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "script-src" not in headers["Content-Security-Policy"]


def test_nested_run_ids_work(runs: Path) -> None:  # noqa: F811
    shutil.copytree(runs / "intended-change", runs / "set-a" / "intended-change")
    httpd, base = serve(runs)
    try:
        status, detail = get(base, "/api/v1/runs/set-a%2Fintended-change", RunDetail)
        assert status == 200 and detail.summary.id == "set-a/intended-change"
        _, page = get(base, "/api/v1/runs?set=set-a", RunPage)
        assert page.run_sets == ["set-a"] and [r.id for r in page.runs] == ["set-a/intended-change"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_overview_and_benchmarks(server) -> None:
    status, overview = get(server, "/api/v1/overview", OverviewMetrics)
    assert status == 200 and overview.total_runs == 2
    assert overview.integrity["intact"] == 2 and overview.job is None
    assert {r.id for r in overview.recent} == {"tenant-cache-take2", "intended-change"}
    status, bench = get(server, "/api/v1/benchmarks", Benchmarks)
    assert status == 200 and bench.benchmarks == [] and len(bench.known_runs) == 2


def test_benchmark_sets_validate() -> None:
    found = dashboard_api.benchmarks(Path(__file__).parent.parent / "bench" / "results")
    kinds = {b.id: b.kind for b in found.benchmarks}
    assert kinds["2026-10-30-run3"] == "run" and kinds["2026-10-30-baselines"] == "baselines"


def test_proof_schema_endpoint(server) -> None:
    status, schema = get(server, "/api/v1/schema/proof")
    assert status == 200 and "verdict" in schema["properties"]


def test_v1_refuses_foreign_hosts_and_other_methods(server) -> None:
    assert fetch(server + "/api/v1/runs", headers={"Host": "evil.example:80"})[0] == 421
    assert fetch(server + "/api/v1/runs", "DELETE")[0] == 405
    assert fetch(server + "/api/v1/verifications", "POST")[0] == 404  # no --repo


def test_spa_routes_serve_the_app_and_unknown_paths_do_not(server) -> None:
    root = fetch(server + "/")[2]
    for path in ("/runs", "/runs/tenant-cache-take2", "/pull-requests", "/verifications/x",
                 "/benchmarks", "/diagnostics"):
        status, headers, body = fetch(server + path)
        assert status == 200 and body == root, path
        assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert fetch(server + "/runsx")[0] == 404
    assert fetch(server + "/static/../dashboard.py")[0] == 404


def test_diagnostics_report_presence_never_values(runs: Path, monkeypatch) -> None:  # noqa: F811
    secret = "sk-SENTINEL-diagnostics-0123456789"
    monkeypatch.setenv("MODEL_API_KEY", secret)
    monkeypatch.setenv("PROOFMERGE_BACKEND", "api")
    monkeypatch.setattr(dashboard_api.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = []

    def runner(argv, **kwargs):
        seen.append(argv)
        # A status command that prints a secret: its output must never reach the page.
        return SimpleNamespace(returncode=0 if argv[0] == "docker" else 1, stdout=secret, stderr=secret)

    httpd, base = serve(runs, runner=runner)
    try:
        status, _, body = fetch(base + "/api/v1/diagnostics")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 200 and secret.encode() not in body
    checks = {c.name: c for c in Diagnostics.model_validate_json(body).checks}
    assert checks["Docker daemon"].ok and checks["Runner image"].ok
    assert checks["Model API key"].ok and checks["Model API key"].detail == "MODEL_API_KEY is set"
    assert checks["GitHub CLI"].ok is False
    assert all(argv[0] in ("docker", "gh") for argv in seen)


def test_diagnostics_without_docker_or_key(runs: Path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("PROOFMERGE_BACKEND", raising=False)
    monkeypatch.setattr(dashboard_api.shutil, "which", lambda name: None)
    found = dashboard_api.diagnostics(runs, None, runner=lambda *a, **k: pytest.fail("ran"))
    checks = {c.name: c for c in found.checks}
    assert checks["Docker daemon"].ok is False and checks["Runner image"].ok is None
    assert checks["Model API key"].ok is False and checks["GitHub CLI"].ok is None


# --- Verification jobs and server-sent events -------------------------------------------

def stage_lines(decision: str = "BLOCK") -> list[str]:
    lines = []
    for name in STAGE_NAMES:
        lines.append(json.dumps({"type": "stage.started", "stage": name, "data": {}}))
        data = {"duration_ms": 5, "errors": 0}
        if name == "deterministic_decide":
            data["decision"] = decision
        lines.append(json.dumps({"type": "stage.completed", "stage": name, "data": data}))
    return lines


class StageRunner:
    """Fakes gh, git and `proofmerge verify`; verify writes stage events, then a proof."""

    def __init__(self, head_sha: str, proof_dir: Path, extra_lines: list[str] | None = None):
        self.head_sha, self.proof_dir, self.extra = head_sha, proof_dir, extra_lines or []
        self.gate: threading.Event | None = None
        self.mid: threading.Event = threading.Event()  # set once half the events are written
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["gh", "repo", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"nameWithOwner": "o/r", "url": "https://github.com/o/r"}), stderr="")
        if argv[:3] == ["gh", "pr", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                [PRS[0] | {"headRefOid": self.head_sha}]), stderr="")
        if "verify" in argv:
            events = Path(argv[argv.index("--events-file") + 1])
            evidence = Path(argv[argv.index("--evidence-dir") + 1])
            lines = self.extra + stage_lines()
            half = len(lines) // 2
            with events.open("a") as handle:
                for i, line in enumerate(lines):
                    if i == half:
                        handle.flush()
                        self.mid.set()
                        if self.gate:
                            self.gate.wait(5)
                    handle.write(line + "\n")
            shutil.copytree(self.proof_dir, evidence)
            return SimpleNamespace(returncode=2, stdout="verdict: BLOCK\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def jobs(runs: Path, tmp_path: Path):  # noqa: F811
    head = json.loads((runs / "tenant-cache-take2" / "proof.json").read_text())["head_sha"]
    runner = StageRunner(head, DEMO / "tenant-cache-take2")
    repo = tmp_path / "repo"
    repo.mkdir()
    httpd, base = serve(runs, repo, runner, heartbeat=0.2)
    yield base, runner
    httpd.shutdown()
    httpd.server_close()


def start(base: str, body: bytes = b'{"number": 7}', **headers) -> tuple[int, dict]:
    token = json.loads(fetch(base + "/api/v1/session")[2])["token"]
    sent = {"Content-Type": "application/json", "Origin": base, "X-ProofMerge-Token": token} | headers
    status, _, data = fetch(base + "/api/v1/verifications", "POST", sent, body)
    return status, json.loads(data)


def wait_done(base: str, job_id: str) -> JobEvents:
    for _ in range(200):
        _, found = get(base, f"/api/v1/jobs/{job_id}", JobEvents)
        if found.job.status != "running":
            return found
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_v1_verification_needs_token_origin_and_strict_json(jobs) -> None:
    base, runner = jobs
    assert start(base, **{"X-ProofMerge-Token": "wrong"})[0] == 403
    assert start(base, Origin="https://evil.example")[0] == 403
    assert start(base, **{"Content-Type": "text/plain"})[0] == 403
    for body in (b'{"number": "7"}', b'{"number": true}', b'{"number": 0}', b'{"number": 7, "ref": "x"}',
                 b"[]", b"{"):
        assert start(base, body)[0] == 400, body
    assert not any("verify" in call for call in runner.calls)


def test_v1_verification_streams_ordered_stage_events(jobs) -> None:
    base, runner = jobs
    status, job = start(base)
    assert status == 202 and job["status"] == "running" and job["pr_number"] == 7
    assert job["job_id"] == job["run_id"] and job["base_branch"] == "main"
    found = wait_done(base, job["job_id"])
    verify = next(c for c in runner.calls if "verify" in c)
    events_file = Path(verify[verify.index("--events-file") + 1])
    evidence = Path(verify[verify.index("--evidence-dir") + 1])
    assert not events_file.resolve().is_relative_to(evidence.resolve())
    ids = [e.event_id for e in found.events]
    assert ids == list(range(1, len(ids) + 1))
    types = [e.type for e in found.events]
    assert types[:3] == ["job.created", "job.fetch_started", "job.fetch_completed"]
    assert types[-1] == "job.completed" and found.job.status == "done"
    stages = [e.stage for e in found.events if e.type == "stage.completed"]
    assert stages == STAGE_NAMES
    # The verdict is announced only after deterministic_decide, never before.
    verdict = types.index("job.verdict_ready")
    decide = next(i for i, e in enumerate(found.events)
                  if e.type == "stage.completed" and e.stage == "deterministic_decide")
    assert verdict == decide + 1 and found.events[verdict].data == {"decision": "BLOCK"}
    assert "job.evidence_written" in types
    assert found.stages == STAGE_NAMES

    # The new run's proof is untouched by events: identical to what verify wrote.
    assert (evidence / "proof.json").read_bytes() == (DEMO / "tenant-cache-take2" / "proof.json").read_bytes()
    _, again = get(base, f"/api/v1/jobs/{job['job_id']}?last_event_id=5", JobEvents)
    assert [e.event_id for e in again.events] == ids[5:]


def read_sse(base: str, path: str, last_id: str | None = None, limit: int = 500) -> list[dict]:
    """Read an SSE response to its end over a raw socket; return the data of each event."""
    host, port = base.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        extra = f"Last-Event-ID: {last_id}\r\n" if last_id else ""
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n{extra}\r\n".encode())
        raw = b""
        while chunk := sock.recv(65536):
            raw += chunk
            if len(raw) > 2_000_000:
                break
    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"200" in head.split(b"\r\n")[0] and b"text/event-stream" in head
    frames = []
    for block in body.decode().split("\n\n"):
        data = [line[6:] for line in block.split("\n") if line.startswith("data: ")]
        ids = [line[4:] for line in block.split("\n") if line.startswith("id: ")]
        if data:
            frames.append(json.loads(data[0]) | {"_sse_id": int(ids[0])})
    return frames[:limit]


def test_sse_replays_from_last_event_id_and_ends_with_the_job(jobs) -> None:
    base, runner = jobs
    runner.gate = threading.Event()
    _, job = start(base)
    assert runner.mid.wait(5)
    received: list[dict] = []
    reader = threading.Thread(target=lambda: received.extend(
        read_sse(base, f"/api/v1/jobs/{job['job_id']}/events")))
    reader.start()
    time.sleep(0.5)  # the stream is open and idle; heartbeats keep it alive
    runner.gate.set()
    reader.join(10)
    assert not reader.is_alive()
    ids = [f["event_id"] for f in received]
    assert ids == list(range(1, len(ids) + 1)) and ids == [f["_sse_id"] for f in received]
    assert received[-1]["type"] == "job.completed"

    # A reconnect with Last-Event-ID gets only what came after it.
    later = read_sse(base, f"/api/v1/jobs/{job['job_id']}/events", last_id="10")
    assert [f["event_id"] for f in later] == ids[10:]


def test_sse_disconnect_releases_the_stream_slot(jobs) -> None:
    base, runner = jobs
    runner.gate = threading.Event()
    _, job = start(base)
    assert runner.mid.wait(5)
    host, port = base.removeprefix("http://").split(":")
    path = f"/api/v1/jobs/{job['job_id']}/events"
    try:
        socks = []
        for _ in range(MAX_STREAMS):
            sock = socket.create_connection((host, int(port)), timeout=5)
            sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            assert b"200" in sock.recv(1024)
            socks.append(sock)
        assert fetch(base + path)[0] == 503  # every slot is taken
        for sock in socks:
            sock.close()
        # Heartbeats hit the closed sockets, the handlers exit and free their slots.
        for _ in range(100):
            sock = socket.create_connection((host, int(port)), timeout=5)
            sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            first = sock.recv(1024)
            sock.close()
            if b"200" in first:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("stream slots were not released")
    finally:
        runner.gate.set()
    assert wait_done(base, job["job_id"]).job.status == "done"


def test_unknown_job_and_jobs_without_repo(jobs, server) -> None:
    base, _ = jobs
    assert fetch(base + "/api/v1/jobs/nope")[0] == 404
    assert fetch(base + "/api/v1/jobs/nope/events")[0] == 404
    assert fetch(server + "/api/v1/jobs/x/events")[0] == 404
    status, _, body = fetch(server + "/api/v1/jobs/active")
    assert status == 200 and json.loads(body) is None


def test_invalid_event_lines_are_dropped(runs: Path, tmp_path: Path) -> None:  # noqa: F811
    head = json.loads((runs / "tenant-cache-take2" / "proof.json").read_text())["head_sha"]
    secret = "sk-SENTINEL-event-line"
    junk = [
        "not json",
        json.dumps({"type": "stage.started", "stage": "rm -rf /", "data": {}}),
        json.dumps({"type": "job.completed", "stage": "write_evidence"}),
        json.dumps({"type": "stage.started", "stage": "resolve_context",
                    "data": {"stdout": secret, "decision": secret, "errors": secret, "duration_ms": -1}}),
        "x" * 5000,
    ]
    runner = StageRunner(head, DEMO / "tenant-cache-take2", junk)
    repo = tmp_path / "repo"
    repo.mkdir()
    httpd, base = serve(runs, repo, runner)
    try:
        _, job = start(base)
        found = wait_done(base, job["job_id"])
        _, _, raw = fetch(base + f"/api/v1/jobs/{job['job_id']}")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert secret.encode() not in raw
    first = next(e for e in found.events if e.stage == "resolve_context")
    assert first.type == "stage.started" and first.data == {}
    assert [e.type for e in found.events].count("job.completed") == 1
    assert all(e.stage in (None, *STAGE_NAMES) for e in found.events)


def test_legacy_endpoints_still_answer_beside_v1(jobs) -> None:
    base, _ = jobs
    assert fetch(base + "/api/index")[0] == 200
    assert fetch(base + "/api/prs")[0] == 200
    pr = get(base, "/api/v1/pull-requests")[1]["prs"][0]
    assert pr["number"] == 7 and pr["runs"] == ["tenant-cache-take2"]
