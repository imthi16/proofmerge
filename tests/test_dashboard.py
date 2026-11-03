import json
import re
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
from proofmerge.dashboard_github import GH_CREDENTIALS
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
    assert "script-src" not in csp and "font-src data:" in csp
    assert fetch(base + "/api/index", "POST")[0] == 404  # no --repo: nothing accepts a POST
    assert fetch(base + "/api/index", "DELETE")[0] == 405
    assert fetch(base + "/api/run?id=../../etc")[0] == 404
    assert fetch(base + "/report?id=tenant-cache-take2/../..")[0] == 404
    assert fetch(base + "/static/../dashboard.py")[0] == 404
    assert fetch(base + "/static/%2e%2e/dashboard.py")[0] == 404


def test_server_serves_the_app_reports_and_run_detail(server) -> None:
    base, _ = server
    status, headers, body = fetch(base + "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    # The previous dashboard stays reachable at /legacy during the migration.
    status, _, body = fetch(base + "/legacy/")
    assert status == 200 and b"/static/legacy/app.js" in body
    assert fetch(base + "/static/legacy/vendor/react.production.min.js")[0] == 200

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
        self.pony_code = 0

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
        if "ponytail-diff" in argv:
            self.pony_argv = argv
            self.pony_intent = Path(argv[argv.index("--intent-file") + 1]).read_text()
            out = Path(argv[argv.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({
                "findings": [{"location": "shop.py:L3", "tag": "yagni", "what": "One-implementation base class.",
                              "replacement": "Inline it."}],
                "net_lines": 9, "run_id": "pr-7", "base_sha": "a" * 40, "head_sha": self.head_sha, "calls": 1}))
            return SimpleNamespace(returncode=self.pony_code, stdout="", stderr="ponytail failed: boom")
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
    # gh's sign-in reaches private repositories for this one command only.
    assert fetch_call[:10] == ["git", "-C", str(repo.resolve()), *GH_CREDENTIALS, "fetch", "--no-tags",
                               "https://github.com/o/r.git"]
    assert "origin" not in fetch_call
    assert next(c for c in runner.calls if c[:3] == ["gh", "pr", "list"])[3:5] == ["--repo", "o/r"]
    assert next(c for c in runner.calls if c[:3] == ["gh", "pr", "list"])[5:7] == ["--state", "open"]
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


def test_pr_verification_uses_session_key_without_exposing_it(tmp_path: Path, monkeypatch) -> None:
    from proofmerge.dashboard_jobs import Jobs
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if "verify" in argv:
            run = Path(argv[argv.index("--evidence-dir") + 1])
            run.mkdir()
            (run / "proof.json").write_text("{}")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    jobs = Jobs(tmp_path)
    jobs.set_key("session-key-for-offline-test")
    prs = PullRequests(tmp_path, tmp_path, runner, jobs.environment)
    prs._remote = ("o/r", "https://github.com/o/r")
    prs.job = {"status": "running"}
    prs._verify({"number": 7, "base": "main", "title": "t", "body": ""}, "pr-test")
    env = next(kw["env"] for argv, kw in calls if "verify" in argv)
    assert env["MODEL_API_KEY"] == "session-key-for-offline-test"
    assert env["PROOFMERGE_JOB_ID"] == "pr-test"
    assert "session-key-for-offline-test" not in json.dumps(prs.job)
    assert "session-key-for-offline-test" not in json.dumps(jobs.settings())
    assert prs.job["status"] == "done"


def test_folder_picker_list_scrolls_inside_the_dialog() -> None:
    # The repository picker's later `.picker { display: grid }` used to win, so the folder list
    # grew past the dialog and scrolled the page. The dialog rule must outrank it.
    css = (Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/app.css").read_text()
    rule = re.search(r"\.dialog\.picker \{([^}]*)\}", css)
    assert rule and "display: flex" in rule.group(1) and "max-height" in rule.group(1)
    assert re.search(r"\.dirs \{[^}]*overflow: auto", css)


def test_app_icon_is_served_and_linked_without_scripts() -> None:
    legacy = Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy"
    icon = (legacy / "icon.svg").read_text()
    assert icon.startswith("<svg") and not re.search(r"<script|<image|href=|onload", icon)
    assert 'rel="icon"' in (legacy / "index.html").read_text()
    assert "/static/legacy/icon.svg" in (legacy / "app.js").read_text()
    app = legacy.parent / "app"  # the Control Center, which answers "/"
    assert (app / "icon.svg").read_text() == icon and "/static/app/icon.svg" in (app / "index.html").read_text()

def test_dialog_stays_fixed_to_the_window() -> None:
    # `.scrim` is position: fixed, but a filling transform animation on `.page` or `.detail` makes
    # Chrome treat that ancestor as the containing block, so the dialog scrolled away with the page.
    css = (Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/app.css").read_text()
    for selector in (r"\.page", r"\.detail"):
        for rule in re.findall(selector + r" \{([^}]*animation:[^}]*)\}", css):
            assert not re.search(r"\b(both|forwards)\b", rule), selector


def test_errors_and_recent_checks_can_be_closed() -> None:
    js = (Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/app.js").read_text()
    assert "function Alert(" in js and "setTimeout(onClose" in js and 'aria-label="Dismiss"' in js
    assert js.count("<${Alert}") >= 5 and 'class="error-box">${posting}' not in js  # action errors do not stay for good
    assert "pm-hidden-recent" in js and "recent-x" in js


def test_repo_picker_declares_every_hook_before_its_early_returns() -> None:
    # A hook after `if (!gh) return …` makes React throw "more hooks than the previous render" once gh
    # loads, and the Pull requests tab goes black.
    js = (Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/app.js").read_text()
    body = js.split("function RepoPicker(", 1)[1].split("\n  function ", 1)[0]
    after = body[body.index("if (!gh) return"):]
    assert not re.search(r"\buse(State|Effect|Ref|Callback|Memo|Context)\(", after)


def post_json(base: str, token: str, path: str, body: dict) -> tuple[int, dict]:
    sent = {"Content-Type": "application/json", "Origin": base, "X-ProofMerge-Token": token}
    status, _, reply = fetch(base + path, "POST", sent, json.dumps(body).encode())
    return status, json.loads(reply)


def wait_for(predicate, seconds: float = 5.0) -> None:
    deadline = time.time() + seconds
    while not predicate():
        assert time.time() < deadline, "timed out"
        time.sleep(0.02)


def test_the_pull_requests_page_runs_a_ponytail_review_without_a_verify_run(pr_server) -> None:
    base, runner, _ = pr_server
    token = json.loads(fetch(base + "/api/session")[2])["token"]
    assert json.loads(fetch(base + "/api/prs")[2])["prs"][0]["ponytail"] is None
    status, job = post_json(base, token, "/api/ponytail", {"number": 7})
    assert status == 202 and job["status"] == "running"
    wait_for(lambda: json.loads(fetch(base + "/api/prs")[2])["ponytail_job"]["status"] == "done")
    pr = json.loads(fetch(base + "/api/prs")[2])["prs"][0]
    assert pr["ponytail"]["findings"][0]["tag"] == "yagni" and pr["ponytail"]["net_lines"] == 9
    assert PRS[0]["title"] in runner.pony_intent  # the PR title and body are the intent
    assert not any("verify" in call for call in runner.calls)  # no run, no sandbox
    assert any(call[:1] == ["git"] and "fetch" in call for call in runner.calls)


def test_a_failed_ponytail_review_says_why_and_a_closed_pr_is_refused(pr_server) -> None:
    base, runner, _ = pr_server
    token = json.loads(fetch(base + "/api/session")[2])["token"]
    runner.pony_code = 1
    assert post_json(base, token, "/api/ponytail", {"number": 7})[0] == 202
    wait_for(lambda: json.loads(fetch(base + "/api/prs")[2])["ponytail_job"]["status"] == "failed")
    assert "boom" in json.loads(fetch(base + "/api/prs")[2])["ponytail_job"]["error"]
    PRS[0]["state"] = "MERGED"
    try:
        wait_for(lambda: json.loads(fetch(base + "/api/prs?refresh=1")[2])["prs"][0]["state"] == "MERGED")
        status, reply = post_json(base, token, "/api/ponytail", {"number": 7})
        assert status == 409 and "only open pull requests" in reply["error"]
    finally:
        PRS[0]["state"] = "OPEN"


def test_the_runs_page_leaves_the_evidence_to_the_full_report() -> None:
    js = (Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/app.js").read_text()
    detail = js.split("function Detail(", 1)[1].split("\n  function ", 1)[0]
    assert 'class="panel evidence"' not in detail and "<${Hypothesis}" not in detail
    assert "/report?id=" in detail and 'aria-label="Suggested fixes"' in detail  # the report link and the fixes stay


def test_posting_and_approving_say_which_github_account_they_act_as() -> None:
    js = (Path(__file__).parents[1] / "src/proofmerge/dashboard_static/legacy/app.js").read_text()
    detail = js.split("function Detail(", 1)[1].split("\n  function ", 1)[0]
    assert "useGitHub()" in detail  # read with the other hooks, before any early return
    assert detail.count('class="as-account"') == 2  # the Post and the Approve buttons name the account
    assert js.count("<${ActingAs}") == 2 and "Use another account" in js  # and so do both confirm dialogs
