"""Run one probe against the base and head revisions, isolated in Docker.

Each revision is exported with `git archive` into a fresh directory, so the user's
repository and its .git are never touched or mounted. The container has no
network, no capabilities, no host environment, a read-only filesystem and
resource limits. Every attempt becomes an `Execution` record with its logs.
"""

import hashlib
import io
import re
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from proofmerge.evidence import Execution, Experiment, Outcome

RUNNER_IMAGE = "proofmerge-runner:0.1"
ATTEMPTS = 3  # a regression must reproduce on every attempt
TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 1_000_000  # per stream; a run that prints more is stopped and is ERROR
OUTPUT_LIMIT_EXIT = -1  # exit code recorded for a run stopped by the output limit
# What runs inside the container; recorded in every Execution.
PYTEST_ARGV = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
INVALID_PROBE_EXIT = 6  # set by OUTCOME_PLUGIN; not a pytest exit code, so it reads ERROR
# Written as conftest.py next to every probe. A test that fails with anything but an
# assertion (NameError, ImportError, a setup error) is a broken probe, not evidence. So
# is a skipped or xfail test: pytest exits 0 for it although its assertion never ran.
OUTCOME_PLUGIN = f"""import pytest

_broken = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    assertion = call.excinfo is not None and call.excinfo.errisinstance(
        (AssertionError, pytest.fail.Exception)
    )
    if report.skipped or hasattr(report, "wasxfail"):
        _broken.append(item.nodeid)
    elif report.failed and (call.when != "call" or not assertion):
        _broken.append(item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if exitstatus in (0, 1) and _broken:
        session.exitstatus = {INVALID_PROBE_EXIT}
"""

SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
HYPOTHESIS_ID = re.compile(r"H[0-9]+")

# (image, code_dir, probe_dir, probe_name, timeout) -> (exit_code or None on timeout, stdout, stderr)
Runner = Callable[[str, Path, Path, str, int], tuple[int | None, bytes, bytes]]


class SandboxError(Exception):
    pass


def export_tree(repo: Path, sha: str, dest: Path) -> None:
    """Write the files of commit `sha` into `dest`, without any .git."""
    if not SHA.fullmatch(sha):
        # Also stops a value like "--output=..." from reaching git as an option.
        raise SandboxError(f"not a full commit SHA: {sha!r}")
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", sha],
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise SandboxError(f"git archive {sha} failed: {archive.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")  # rejects absolute paths, .. and device files


def open_to_runner(root: Path) -> None:
    """Let the container user read `root`, whatever the host umask was."""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue  # chmod would follow it; the data filter keeps links inside the tree
        path.chmod(0o755 if path.is_dir() else 0o644)


def outcome_for(exit_code: int | None) -> Outcome:
    if exit_code is None:
        return "TIMEOUT"
    # Only pytest exit 1 is a test failure. Anything else (collection error, no tests,
    # docker error 125-127, OOM kill 137) is an execution error, never evidence.
    return {0: "PASS", 1: "FAIL"}.get(exit_code, "ERROR")


def image_digest(image: str = RUNNER_IMAGE) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SandboxError(
            f"runner image {image} not found; build it with `docker build -t {image} docker/runner`"
        )
    return result.stdout.strip()


def docker_argv(
    name: str, image: str, code_dir: Path, probe_dir: Path, probe_name: str
) -> list[str]:
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--pids-limit", "64",
        "--memory", "512m", "--memory-swap", "512m",
        "--cpus", "1",
        "--env", "HOME=/tmp",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        # shortcut: assumes a src/ or flat layout; repos that need installing come later.
        "--env", "PYTHONPATH=/work/code/src:/work/code",
        "--volume", f"{code_dir}:/work/code:ro",
        "--volume", f"{probe_dir}:/work/probes:ro",
        "--workdir", "/work/code",
        image,
        *PYTEST_ARGV, f"/work/probes/{probe_name}",
    ]  # fmt: skip


def _read_capped(stream, chunks: list[bytes], over: threading.Event) -> None:
    size = 0
    while chunk := stream.read(65536):
        if size < MAX_OUTPUT_BYTES:
            chunks.append(chunk[: MAX_OUTPUT_BYTES - size])
        size += len(chunk)
        if size > MAX_OUTPUT_BYTES:
            over.set()


def docker_runner(
    image: str, code_dir: Path, probe_dir: Path, probe_name: str, timeout: int
) -> tuple[int | None, bytes, bytes]:
    """Run one probe; output is read in bounded chunks so a noisy probe cannot fill host memory."""
    name = f"proofmerge-{uuid.uuid4().hex[:12]}"
    argv = docker_argv(name, image, code_dir, probe_dir, probe_name)
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out: list[bytes] = []
    err: list[bytes] = []
    over = threading.Event()
    readers = [
        threading.Thread(target=_read_capped, args=(proc.stdout, out, over)),
        threading.Thread(target=_read_capped, args=(proc.stderr, err, over)),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    while proc.poll() is None and not over.is_set() and time.monotonic() < deadline:
        over.wait(0.1)
    timed_out = proc.poll() is None and not over.is_set()
    if proc.poll() is None:
        # Killing the CLI would leave the container running; kill it by name.
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for reader in readers:
        reader.join()
    stdout, stderr = b"".join(out), b"".join(err)
    if over.is_set():
        note = f"\n[proofmerge: stopped, output over {MAX_OUTPUT_BYTES} bytes]\n".encode()
        return OUTPUT_LIMIT_EXIT, stdout, stderr + note
    return (None if timed_out else proc.returncode), stdout, stderr


def run_experiment(
    repo: Path,
    base_sha: str,
    head_sha: str | None,
    hypothesis_id: str,
    probe_source: str,
    evidence_dir: Path,
    *,
    runner: Runner = docker_runner,
    digest: str | None = None,
    attempts: int = ATTEMPTS,
    timeout: int = TIMEOUT_SECONDS,
) -> Experiment:
    """Run `probe_source` `attempts` times on each revision; logs go to `evidence_dir`.

    With `head_sha` None only base runs, so a probe can be checked without showing head.
    """
    if not HYPOTHESIS_ID.fullmatch(hypothesis_id):
        raise SandboxError(f"bad hypothesis id: {hypothesis_id!r}")  # it becomes a file name
    if digest is None:
        digest = image_digest()  # runs use this ID, not the tag, so a rebuild cannot slip in
    evidence_dir = evidence_dir.resolve()  # docker reads a relative bind source as a volume name
    probe_name = f"test_{hypothesis_id.lower()}.py"
    probe_dir = evidence_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / probe_name).write_text(probe_source)
    (probe_dir / "conftest.py").write_text(OUTCOME_PLUGIN)
    open_to_runner(probe_dir)
    log_dir = evidence_dir / hypothesis_id
    log_dir.mkdir(exist_ok=True)

    executions: list[Execution] = []
    with tempfile.TemporaryDirectory(prefix="proofmerge-") as tmp:
        Path(tmp).chmod(0o755)  # the container user (uid 65534) is not the host user
        revisions = [("base", base_sha)] + ([("head", head_sha)] if head_sha else [])
        for revision, sha in revisions:
            code_dir = Path(tmp) / revision
            export_tree(repo, sha, code_dir)
            open_to_runner(code_dir)
            for attempt in range(1, attempts + 1):
                start = time.monotonic()
                exit_code, stdout, stderr = runner(digest, code_dir, probe_dir, probe_name, timeout)
                duration_ms = round((time.monotonic() - start) * 1000)
                stem = f"{revision}-{attempt}"
                (log_dir / f"{stem}.stdout").write_bytes(stdout)
                (log_dir / f"{stem}.stderr").write_bytes(stderr)
                executions.append(
                    Execution(
                        revision=revision,
                        commit_sha=sha,
                        attempt=attempt,
                        command_argv=[*PYTEST_ARGV, f"probes/{probe_name}"],
                        exit_code=exit_code,
                        outcome=outcome_for(exit_code),
                        duration_ms=duration_ms,
                        stdout_path=f"{hypothesis_id}/{stem}.stdout",
                        stderr_path=f"{hypothesis_id}/{stem}.stderr",
                        runner_image_digest=digest,
                    )
                )
    return Experiment(
        hypothesis_id=hypothesis_id,
        test_source_path=f"probes/{probe_name}",
        test_source_sha256=hashlib.sha256(probe_source.encode()).hexdigest(),
        timeout_seconds=timeout,
        executions=executions,
    )
