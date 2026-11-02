import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from proofmerge.dashboard import make_server
from proofmerge.dashboard_jobs import JobError, Jobs, branches, browse, check_ref, git_project


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "shop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "feature/fast-cache")
    (repo / "a.py").write_text("x = 2\n")
    git(repo, "commit", "-qam", "change")
    return repo


class FakeProcess:
    """Popen stand-in: prints the lines it was given and exits with a code."""

    def __init__(self, lines: list[str], code: int, gate: threading.Event | None = None) -> None:
        self.lines, self.code, self.gate, self.killed = lines, code, gate, False

    @property
    def stdout(self):
        yield from self.lines
        if self.gate is not None:
            self.gate.wait(5)

    def wait(self) -> int:
        return -9 if self.killed else self.code

    def kill(self) -> None:
        self.killed = True
        if self.gate is not None:
            self.gate.set()


def wait_done(jobs: Jobs) -> dict:
    for _ in range(200):
        if jobs.job and jobs.job["finished"]:
            return jobs.job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_project_must_be_a_git_work_tree(project: Path, tmp_path: Path) -> None:
    assert git_project(str(project / ".")) == project.resolve()
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(JobError, match="not a Git project"):
        git_project(str(plain))
    with pytest.raises(JobError, match="not a folder"):
        git_project("relative/path")


@pytest.mark.parametrize("ref", ["-c", "--output=x", "main..feature/fast-cache", "no-such", "a b"])
def test_refs_that_are_options_ranges_or_unknown_are_refused(project: Path, ref: str) -> None:
    with pytest.raises(JobError):
        check_ref(project, ref)


def test_branches_guess_main_as_base_and_the_change_as_head(project: Path) -> None:
    found = branches(project)
    assert found["base"] == "main" and found["head"] == "feature/fast-cache"
    assert {b["name"] for b in found["branches"]} == {"main", "feature/fast-cache"}


def test_browse_lists_folders_marks_git_and_hides_dotfolders(project: Path, tmp_path: Path) -> None:
    (tmp_path / ".secret").mkdir()
    listed = browse(str(tmp_path))
    names = {d["name"]: d["git"] for d in listed["dirs"]}
    assert names.get("shop") is True and ".secret" not in names
    with pytest.raises(JobError):
        browse("not/absolute")


def test_verify_job_runs_the_cli_with_a_server_named_run(project: Path, tmp_path: Path) -> None:
    seen = {}

    def spawn(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        run = Path(argv[argv.index("--evidence-dir") + 1])
        run.mkdir(parents=True)
        (run / "proof.json").write_text("{}")
        return FakeProcess(["verdict: BLOCK\n"], 2)

    jobs = Jobs(tmp_path / "runs", spawn=spawn)
    jobs.set_key("sk-test-key-123456")
    jobs.start({"command": "verify", "project": str(project), "base": "main",
                "head": "feature/fast-cache", "intent": "Speed up the cache."}, {})
    job = wait_done(jobs)
    argv = seen["argv"]
    assert argv[3:7] == ["verify", str(project.resolve()), "--base", "main"]
    assert "--intent-file" in argv  # the description reaches the CLI as a file
    assert Path(argv[argv.index("--evidence-dir") + 1]).parent == (tmp_path / "runs").resolve()
    assert job["status"] == "done" and job["exit_code"] == 2  # BLOCK is a finished run
    assert seen["env"]["MODEL_API_KEY"] == "sk-test-key-123456"
    assert "sk-test" not in json.dumps(jobs.settings()) and jobs.progress()["stage"] == 5
    # Only the run folder and proof exist, so the page shows the middle steps as not run.
    assert jobs.progress()["seen"] == [True, False, False, False, True]


def test_same_branch_twice_and_unknown_commands_are_refused(project: Path, tmp_path: Path) -> None:
    jobs = Jobs(tmp_path / "runs", spawn=lambda *a, **k: FakeProcess([], 0))
    with pytest.raises(JobError, match="two different"):
        jobs.start({"command": "verify", "project": str(project), "base": "main", "head": "main"}, {})
    with pytest.raises(JobError, match="Unknown"):
        jobs.start({"command": "rm"}, {})


def test_one_job_at_a_time_and_stop_ends_it(tmp_path: Path) -> None:
    gate = threading.Event()
    jobs = Jobs(tmp_path, runner=lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""),
                spawn=lambda *a, **k: FakeProcess(["building\n"], 0, gate))
    jobs.start({"command": "setup"}, {})
    with pytest.raises(JobError, match="still running"):
        jobs.start({"command": "smoke"}, {})
    time.sleep(0.05)
    jobs.cancel()
    job = wait_done(jobs)
    assert job["status"] == "stopped" and "building" in job["output"]


def fetch(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_folder_listing_and_jobs_need_the_session_token(project: Path, tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir()
    httpd = make_server(tmp_path / "runs", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        token = fetch(base + "/api/session")[1]["token"]
        assert fetch(base + "/api/browse")[0] == 403
        status, listed = fetch(base + "/api/browse?path=" + str(tmp_path), headers={"X-ProofMerge-Token": token})
        assert status == 200 and any(d["name"] == "shop" for d in listed["dirs"])
        post = {"Content-Type": "application/json", "Origin": base}
        body = json.dumps({"command": "smoke"}).encode()
        assert fetch(base + "/api/jobs", "POST", post, body)[0] == 403
        status, reply = fetch(base + "/api/settings", "POST", post | {"X-ProofMerge-Token": token},
                              json.dumps({"project": str(project)}).encode())
        assert status == 200 and reply["prs"] is True
        assert reply["settings"]["project"] == str(project.resolve())
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_rerun_stop_kills_containers_with_its_own_label(tmp_path: Path) -> None:
    gate = threading.Event()
    spawned = threading.Event()
    calls = []
    envs = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "container1\n", "")

    def spawn(argv, **kwargs):
        envs.append(kwargs["env"])
        spawned.set()
        return FakeProcess([], 0, gate)

    jobs = Jobs(tmp_path, runner=runner, spawn=spawn)
    # A rerun has no new evidence directory, just like replay/setup.
    job = {"id": "rerun-job-123", "run": "", "command": "rerun", "status": "running",
           "output": "", "finished": None}
    jobs.job = job
    worker = threading.Thread(target=jobs._work, args=(job, ["fake-cli"], ""))
    worker.start()
    assert spawned.wait(2)
    for _ in range(100):
        if jobs._process is not None:
            break
        time.sleep(0.01)
    jobs.cancel()
    worker.join(2)
    assert not worker.is_alive()
    assert envs[0]["PROOFMERGE_JOB_ID"] == "rerun-job-123"
    assert ["docker", "ps", "-q", "--filter", "label=proofmerge.job=rerun-job-123"] in calls
    assert ["docker", "kill", "container1"] in calls


def test_project_switch_cannot_orphan_running_pr(project: Path, tmp_path: Path) -> None:
    httpd = make_server(tmp_path, 0)
    handler = httpd.RequestHandlerClass
    from proofmerge.dashboard_prs import PullRequests
    handler.prs = PullRequests(project, tmp_path, environment=handler.jobs.environment)
    original = handler.prs
    original.job = {"status": "running", "number": 7}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    headers = {"Origin": base, "Content-Type": "application/json",
               "X-ProofMerge-Token": handler.token}
    try:
        status, _ = fetch(base + "/api/settings", "POST", headers,
                          json.dumps({"project": str(project)}).encode())
        assert status == 422 and handler.prs is original
        assert fetch(base + "/api/jobs", "POST", headers,
                     json.dumps({"command": "smoke"}).encode())[0] == 422
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_pr_and_home_start_requests_share_one_lock(tmp_path: Path) -> None:
    from types import SimpleNamespace

    httpd = make_server(tmp_path, 0)
    handler = httpd.RequestHandlerClass
    entered, release, home_started = threading.Event(), threading.Event(), threading.Event()
    prs = SimpleNamespace(job=None)

    def start_pr(number):
        entered.set()
        assert release.wait(2)
        prs.job = {"status": "running", "number": number}
        return prs.job

    def start_home(*args):
        home_started.set()

    prs.start = start_pr
    handler.prs = prs
    handler.jobs.start = start_home
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    headers = {"Origin": base, "Content-Type": "application/json",
               "X-ProofMerge-Token": handler.token}
    results = {}

    def request(name, route, body):
        results[name] = fetch(base + route, "POST", headers, json.dumps(body).encode())[0]

    first = threading.Thread(target=request, args=("pr", "/api/verify", {"number": 7}))
    second = threading.Thread(target=request, args=("home", "/api/jobs", {"command": "smoke"}))
    try:
        first.start()
        assert entered.wait(2)
        second.start()
        assert not home_started.wait(0.1)
        release.set()
        first.join(2)
        second.join(2)
        assert results == {"pr": 202, "home": 422}
        assert not home_started.is_set()
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
        httpd.shutdown()
        httpd.server_close()


def test_control_center_and_browser_app_keep_separate_entrypoints(tmp_path: Path) -> None:
    httpd = make_server(tmp_path, 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        for path, expected in [("/", b"/static/app/assets/"),
                               ("/app/", b"/static/legacy/app.js"),
                               ("/legacy/", b"/static/legacy/app.js")]:
            with urllib.request.urlopen(base + path) as response:
                assert expected in response.read()
        with urllib.request.urlopen(base + "/static/legacy/vendor/geist-latin-wght-normal.woff2") as response:
            assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("route", ["/api/verify", "/api/v1/verifications"])
def test_both_pr_apis_refuse_to_overlap_home_job(tmp_path: Path, route: str) -> None:
    from types import SimpleNamespace
    httpd = make_server(tmp_path, 0)
    handler = httpd.RequestHandlerClass
    handler.jobs.job = {"status": "running"}
    handler.prs = SimpleNamespace(job=None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    headers = {"Origin": base, "Content-Type": "application/json",
               "X-ProofMerge-Token": handler.token}
    try:
        assert fetch(base + route, "POST", headers, b'{"number": 7}')[0] == 409
    finally:
        httpd.shutdown()
        httpd.server_close()
