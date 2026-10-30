import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.dashboard import _benchmarks, _detail, _summary, make_server, scan
from proofmerge.dashboard_prs import PrError, PullRequests
from proofmerge.sandbox import docker_argv

DEMO = Path(__file__).parent.parent / "demo" / "runs" / "2026-10-29"


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    for name in ("tenant-cache-take2", "intended-change"):
        shutil.copytree(DEMO / name, root / name)
    return root


def test_scan_finds_every_run_and_skips_symlinks_out(runs: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(runs / "intended-change" / "proof.json", outside / "proof.json")
    (runs / "sneaky").mkdir()
    (runs / "sneaky" / "proof.json").symlink_to(outside / "proof.json")

    assert sorted(scan(runs)) == ["intended-change", "tenant-cache-take2"]


def test_summary_reports_verdict_and_integrity(runs: Path) -> None:
    summary = _summary("tenant-cache-take2", runs / "tenant-cache-take2")
    assert (summary["decision"], summary["regressions"], summary["has_report"]) == ("BLOCK", 2, True)
    assert summary["integrity"] == {"status": "intact", "problems": []}

    probe = next((runs / "tenant-cache-take2" / "probes").rglob("*.py"))
    probe.write_text(probe.read_text() + "\n# edited\n")
    assert _summary("tenant-cache-take2", runs / "tenant-cache-take2")["integrity"]["status"] == "changed"


def test_summary_gives_an_absolute_proof_path_for_replay(runs: Path) -> None:
    path = _summary("intended-change", runs / "intended-change")["proof_path"]
    assert Path(path).is_absolute() and path.endswith("/intended-change/proof.json")


def test_unreadable_proof_is_an_error_row_not_a_crash(runs: Path) -> None:
    (runs / "broken").mkdir()
    (runs / "broken" / "proof.json").write_text("{")
    assert "could not be read" in _summary("broken", runs / "broken")["error"]


def test_benchmark_label_comes_from_results_next_to_the_run(runs: Path) -> None:
    (runs / "results.json").write_text(json.dumps({"candidates": {
        "tenant-cache-take2": {"label": "BLOCK", "held_out": True, "decision": "BLOCK"},
    }}))
    assert _summary("tenant-cache-take2", runs / "tenant-cache-take2")["benchmark"] == {
        "label": "BLOCK", "held_out": True}
    assert _summary("intended-change", runs / "intended-change")["benchmark"] is None


def test_benchmarks_read_run_and_baseline_results() -> None:
    found = {b["id"]: b for b in _benchmarks(Path(__file__).parent.parent / "bench" / "results")}
    assert found["2026-10-30-run3"]["kind"] == "run"
    assert len(found["2026-10-30-run3"]["candidates"]) == 28
    assert found["2026-10-30-baselines"]["kind"] == "baselines"
    assert {"a", "b"} <= found["2026-10-30-baselines"]["candidates"][0].keys()


def test_detail_carries_probe_source_and_head_failure(runs: Path) -> None:
    detail = _detail("tenant-cache-take2", runs / "tenant-cache-take2")
    assert "def test_" in detail["probes"]["H1"]
    assert any("DID NOT RAISE" in line for line in detail["failures"]["H1"])
    log = detail["logs"]["H1:head:1"]
    assert log["outcome"] == "FAIL" and "DID NOT RAISE" in log["stdout"] and log["command"]


@pytest.fixture
def server(runs: Path):
    httpd = make_server(runs, 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", httpd
    httpd.shutdown()
    httpd.server_close()


def fetch(url: str, method: str = "GET", headers: dict | None = None,
          body: bytes | None = None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_server_is_local_and_read_only(server) -> None:
    base, httpd = server
    assert httpd.server_address[0] == "127.0.0.1"

    status, headers, body = fetch(base + "/api/index")
    assert status == 200 and "default-src 'self'" in headers["Content-Security-Policy"]
    assert {r["id"] for r in json.loads(body)["runs"]} == {"intended-change", "tenant-cache-take2"}

    csp = fetch(base + "/report?id=tenant-cache-take2")[1]["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "style-src 'unsafe-inline'" in csp
    assert "script-src" not in csp
    assert fetch(base + "/api/index", "POST")[0] == 404  # no --repo: nothing accepts a POST
    assert fetch(base + "/api/index", "DELETE")[0] == 405
    assert fetch(base + "/api/run?id=../../etc")[0] == 404
    assert fetch(base + "/report?id=tenant-cache-take2/../..")[0] == 404
    assert fetch(base + "/static/../dashboard.py")[0] == 404
    assert fetch(base + "/static/%2e%2e/dashboard.py")[0] == 404


def test_server_serves_the_app_reports_and_run_detail(server) -> None:
    base, _ = server
    status, _, body = fetch(base + "/")
    assert status == 200 and b"/static/app.js" in body
    assert fetch(base + "/static/vendor/react.production.min.js")[0] == 200

    status, headers, body = fetch(base + "/report?id=tenant-cache-take2")
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"BLOCK" in body

    status, _, body = fetch(base + "/api/run?id=intended-change")
    assert status == 200 and json.loads(body)["proof"]["verdict"]["decision"] == "REVIEW"


def test_foreign_host_header_is_refused(server) -> None:
    base, _ = server
    assert fetch(base + "/api/index", headers={"Host": "evil.example:80"})[0] == 421


PRS = [{"number": 7, "title": "Cache prices", "author": {"login": "dev"}, "headRefName": "agent/x",
        "baseRefName": "main", "headRefOid": "", "state": "OPEN", "url": "https://github.com/o/r/pull/7",
        "updatedAt": "2026-10-30T00:00:00Z", "isDraft": False, "body": "Keep prices the same."}]


class FakeRunner:
    def __init__(self, head_sha: str, verify_code: int = 2):
        self.head_sha, self.verify_code, self.calls = head_sha, verify_code, []
        self.done = threading.Event()
        self.gate: threading.Event | None = None  # when set, verify waits for it

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["gh", "repo", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"nameWithOwner": "o/r", "url": "https://github.com/o/r"}), stderr="")
        if argv[:3] == ["gh", "pr", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                [PRS[0] | {"headRefOid": self.head_sha}]), stderr="")
        if "verify" in argv:
            if self.gate:
                self.gate.wait(5)
            self.intent = Path(argv[argv.index("--intent-file") + 1]).read_text()
            evidence = Path(argv[argv.index("--evidence-dir") + 1])
            evidence.mkdir()
            (evidence / "proof.json").write_text("{}")
            self.done.set()
            return SimpleNamespace(returncode=self.verify_code, stdout="verdict: BLOCK\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def pr_server(runs: Path, tmp_path: Path):
    head = json.loads((runs / "tenant-cache-take2" / "proof.json").read_text())["head_sha"]
    runner = FakeRunner(head)
    repo = tmp_path / "repo"
    repo.mkdir()
    httpd = make_server(runs, 0, repo, runner)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    yield base, runner, repo
    httpd.shutdown()
    httpd.server_close()


def post_verify(base: str, token: str, number=7, origin: str | None = None, **headers) -> int:
    body = json.dumps({"number": number}).encode()
    sent = {"Content-Type": "application/json", "Origin": origin or base,
            "X-ProofMerge-Token": token} | headers
    return fetch(base + "/api/verify", "POST", sent, body)[0]


def test_prs_show_the_run_for_their_head_commit(pr_server) -> None:
    base, _, _ = pr_server
    status, _, body = fetch(base + "/api/prs")
    pr = json.loads(body)["prs"][0]
    assert status == 200 and pr["number"] == 7 and pr["author"] == "dev"
    assert pr["runs"] == ["tenant-cache-take2"]
    assert pr["older_runs"] == []


def test_prs_list_runs_from_earlier_commits(pr_server, runs: Path) -> None:
    base, _, _ = pr_server
    shutil.copytree(runs / "intended-change", runs / "pr-7-0000000-1")
    pr = json.loads(fetch(base + "/api/prs")[2])["prs"][0]
    assert pr["older_runs"] == ["pr-7-0000000-1"]


def test_ids_endpoint_lists_runs_without_parsing(server, runs: Path) -> None:
    base, _ = server
    (runs / "new-run").mkdir()
    (runs / "new-run" / "proof.json").write_text("{")
    assert json.loads(fetch(base + "/api/ids")[2]) == ["intended-change", "new-run", "tenant-cache-take2"]


def test_verify_needs_token_origin_and_json(pr_server) -> None:
    base, runner, _ = pr_server
    token = json.loads(fetch(base + "/api/session")[2])["token"]
    assert post_verify(base, "wrong") == 403
    assert post_verify(base, token, origin="https://evil.example") == 403
    assert post_verify(base, token, **{"Content-Type": "text/plain"}) == 403
    assert post_verify(base, token, number="7") == 400
    assert not any("verify" in call for call in runner.calls)


def test_verify_fetches_into_proofmerge_refs_and_runs_verify(pr_server, runs: Path) -> None:
    base, runner, repo = pr_server
    token = json.loads(fetch(base + "/api/session")[2])["token"]
    assert post_verify(base, token) == 202
    assert runner.done.wait(5)

    fetch_call = next(c for c in runner.calls if "fetch" in c)
    assert fetch_call[:6] == ["git", "-C", str(repo.resolve()), "fetch", "--no-tags",
                              "https://github.com/o/r.git"]
    assert "origin" not in fetch_call
    assert next(c for c in runner.calls if c[:3] == ["gh", "pr", "list"])[3:5] == ["--repo", "o/r"]
    assert "+refs/pull/7/head:refs/proofmerge/pr-7" in fetch_call
    assert "+refs/heads/main:refs/proofmerge/base-7" in fetch_call
    verify = next(c for c in runner.calls if "verify" in c)
    assert verify[verify.index("--head") + 1] == "refs/proofmerge/pr-7"
    assert Path(verify[verify.index("--evidence-dir") + 1]).parent == runs.resolve()
    assert runner.intent == "Cache prices\n\nKeep prices the same."
    for _ in range(50):
        job = json.loads(fetch(base + "/api/prs")[2])["job"]
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert (job["status"], job["exit_code"]) == ("done", 2)


def test_second_verify_while_running_is_refused(pr_server) -> None:
    base, runner, _ = pr_server
    token = json.loads(fetch(base + "/api/session")[2])["token"]
    runner.gate = threading.Event()
    assert post_verify(base, token) == 202
    assert post_verify(base, token) == 409
    runner.gate.set()


def test_closed_prs_cannot_be_verified(pr_server) -> None:
    base, runner, _ = pr_server
    PRS[0]["state"] = "MERGED"
    try:
        token = json.loads(fetch(base + "/api/session")[2])["token"]
        assert post_verify(base, token) == 409
    finally:
        PRS[0]["state"] = "OPEN"
    assert not any("fetch" in call for call in runner.calls)


def test_gh_repo_view_with_odd_values_is_refused(tmp_path: Path) -> None:
    for out in ({"nameWithOwner": "o/r --web", "url": "https://x/o/r"},
                {"nameWithOwner": "o/r", "url": "ssh://git@x/o/r"}):
        runner = lambda argv, out=out, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
        with pytest.raises(PrError):
            PullRequests(tmp_path, tmp_path, runner).pulls()


def test_docker_argv_labels_only_a_valid_job_id(monkeypatch) -> None:
    def argv() -> list[str]:
        return docker_argv("n", "img", Path("/c"), Path("/p"), "t.py")

    monkeypatch.delenv("PROOFMERGE_JOB_ID", raising=False)
    assert "--label" not in argv()
    monkeypatch.setenv("PROOFMERGE_JOB_ID", "pr-7-abc1234-1")
    assert argv()[argv().index("--label") + 1] == "proofmerge.job=pr-7-abc1234-1"
    monkeypatch.setenv("PROOFMERGE_JOB_ID", "bad id;rm")
    assert "--label" not in argv()


def test_verify_timeout_kills_the_jobs_containers(tmp_path: Path) -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:3] == ["docker", "ps", "-q"]:
            return SimpleNamespace(returncode=0, stdout="abc\ndef\n", stderr="")
        if "verify" in argv:
            raise subprocess.TimeoutExpired(argv, 1)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    prs = PullRequests(tmp_path, tmp_path, runner)
    prs._remote = ("o/r", "https://github.com/o/r")
    prs.job = {"status": "running", "step": "", "exit_code": None, "output": ""}
    prs._verify({"number": 7, "base": "main", "title": "t", "body": ""}, "pr-7-abc1234-1")

    verify = next(kw for argv, kw in calls if "verify" in argv)
    assert verify["env"]["PROOFMERGE_JOB_ID"] == "pr-7-abc1234-1"
    assert ["docker", "ps", "-q", "--filter", "label=proofmerge.job=pr-7-abc1234-1"] in [a for a, _ in calls]
    assert [a for a, _ in calls if a[:2] == ["docker", "kill"]] == [
        ["docker", "kill", "abc"], ["docker", "kill", "def"]]
    assert prs.job["status"] == "failed" and "containers were stopped" in prs.job["step"]
