"""Run one probe against the base and head revisions, isolated in Docker.

Each revision is exported with `git archive` into a fresh directory, so the user's
repository and its .git are never touched or mounted. The container has no
network, no capabilities, no host environment, a read-only filesystem and
resource limits. Every attempt becomes an `Execution` record with its logs.
"""

import hashlib
import io
import os
import re
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from proofmerge.evidence import Execution, Experiment, Outcome
from proofmerge.project_config import ProjectConfig

RUNNER_IMAGE = "proofmerge-runner:0.1"
ATTEMPTS = 3  # a regression must reproduce on every attempt
TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 1_000_000  # per stream; a run that prints more is stopped and is ERROR
OUTPUT_LIMIT_EXIT = -1  # exit code recorded for a run stopped by the output limit
# What runs inside the container; recorded in every Execution.
PYTEST_ARGV = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
INVALID_PROBE_EXIT = 6  # set by OUTCOME_PLUGIN; not a pytest exit code, so it reads ERROR
# Runner used for repository-defined languages in `.proofmerge.toml`.
ConfigRunner = Callable[[ProjectConfig, Path, str, int], tuple[int | None, bytes, bytes]]
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
HYPOTHESIS_ID = re.compile(r"[HT][0-9]+")
JOB_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")

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
        # Keep git's executable bit so a repo script (./gradlew) can still run.
        executable = path.is_dir() or path.stat().st_mode & 0o100
        path.chmod(0o755 if executable else 0o644)


def outcome_for(exit_code: int | None) -> Outcome:
    if exit_code is None:
        return "TIMEOUT"
    # Only pytest exit 1 is a test failure. Anything else (collection error, no tests,
    # docker error 125-127, OOM kill 137) is an execution error, never evidence.
    return {0: "PASS", 1: "FAIL"}.get(exit_code, "ERROR")


def is_runner_failure(exit_code: int | None) -> bool:
    """True when Docker or the container runtime failed, not pytest inside the container."""
    if exit_code is None:
        return False
    return exit_code in (125, 126, 127, 137)


def image_digest(image: str = RUNNER_IMAGE) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SandboxError(
            f"runner image {image} not found; run `proofmerge setup` to build it"
        )
    return result.stdout.strip()


def runner_dockerfile_path() -> Path:
    """Return the packaged Dockerfile, or the repo copy when running from source."""
    resource = resources.files("proofmerge") / "runner" / "Dockerfile"
    if resource.is_file():
        return Path(resource)
    # Source checkout fallback: sandbox.py is at src/proofmerge/sandbox.py.
    repo_root = Path(__file__).resolve().parent.parent.parent
    fallback = repo_root / "docker" / "runner" / "Dockerfile"
    if fallback.is_file():
        return fallback
    raise SandboxError("runner Dockerfile not found; is the package installed correctly?")


def docker_argv(
    name: str, image: str, code_dir: Path, probe_dir: Path, probe_name: str
) -> list[str]:
    # The dashboard sets this so it can find and kill the containers of a verify it timed out.
    job = os.environ.get("PROOFMERGE_JOB_ID", "")
    label = ["--label", f"proofmerge.job={job}"] if JOB_ID.fullmatch(job) else []
    return [
        "docker", "run", "--rm", "--name", name, *label,
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


def config_docker_argv(name: str, config: ProjectConfig, code_dir: Path) -> list[str]:
    """Docker argv for a repository-defined test command."""
    job = os.environ.get("PROOFMERGE_JOB_ID", "")
    label = ["--label", f"proofmerge.job={job}"] if JOB_ID.fullmatch(job) else []
    env: list[str] = []
    for key, value in config.env.items():
        env.extend(["--env", f"{key}={value}"])
    return [
        "docker", "run", "--rm", "--name", name, *label,
        "--network", "none",
        "--read-only",
        "--tmpfs", f"/tmp:rw,exec,size={config.tmpfs_mb}m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--pids-limit", "64",
        "--memory", "512m", "--memory-swap", "512m",
        "--cpus", "1",
        *env,
        "--volume", f"{code_dir}:/work:ro",
        "--workdir", "/work",
        config.image,
        *config.test_command,
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


def config_docker_runner(
    config: ProjectConfig, code_dir: Path, probe_source: str, timeout: int
) -> tuple[int | None, bytes, bytes]:
    """Run one config-defined probe; the probe is written into the read-only export first."""
    name = f"proofmerge-{uuid.uuid4().hex[:12]}"
    probe_path = code_dir / config.probe_path
    probe_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path.write_text(probe_source, encoding="utf-8")
    open_to_runner(code_dir)
    argv = config_docker_argv(name, config, code_dir)
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


def outcome_for_config(
    exit_code: int | None, config: ProjectConfig, stdout: bytes, stderr: bytes
) -> Outcome:
    """Map a config-defined test result to PASS/FAIL/ERROR/TIMEOUT.

    A non-zero exit is FAIL only when `assertion_failure` matches the output;
    otherwise it is ERROR. Runner failures stay ERROR, never evidence.
    """
    if exit_code is None:
        return "TIMEOUT"
    if exit_code in config.pass_exit_codes:
        return "PASS"
    if is_runner_failure(exit_code) or exit_code == OUTPUT_LIMIT_EXIT:
        return "ERROR"
    combined = (stdout + stderr).decode(errors="replace")
    if re.search(config.assertion_failure, combined):
        return "FAIL"
    return "ERROR"


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
    (probe_dir / probe_name).write_text(probe_source, encoding="utf-8")
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


def run_config_experiment(
    repo: Path,
    base_sha: str,
    head_sha: str | None,
    hypothesis_id: str,
    probe_source: str,
    config: ProjectConfig,
    evidence_dir: Path,
    *,
    runner: ConfigRunner = config_docker_runner,
    digest: str | None = None,
    attempts: int = ATTEMPTS,
    timeout: int = TIMEOUT_SECONDS,
) -> Experiment:
    """Run a config-defined probe `attempts` times on each revision."""
    if not HYPOTHESIS_ID.fullmatch(hypothesis_id):
        raise SandboxError(f"bad hypothesis id: {hypothesis_id!r}")
    if digest is None:
        digest = image_digest(config.image)
    # Run the image ID that is recorded, not a tag that could be retagged mid-run.
    pinned = config.model_copy(update={"image": digest})
    evidence_dir = evidence_dir.resolve()
    suffix = Path(config.probe_path).suffix or ""
    probe_name = f"test_{hypothesis_id.lower()}{suffix}"
    probe_dir = evidence_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / probe_name).write_text(probe_source, encoding="utf-8")
    open_to_runner(probe_dir)
    log_dir = evidence_dir / hypothesis_id
    log_dir.mkdir(exist_ok=True)

    executions: list[Execution] = []
    with tempfile.TemporaryDirectory(prefix="proofmerge-") as tmp:
        Path(tmp).chmod(0o755)
        revisions = [("base", base_sha)] + ([("head", head_sha)] if head_sha else [])
        for revision, sha in revisions:
            code_dir = Path(tmp) / revision
            export_tree(repo, sha, code_dir)
            open_to_runner(code_dir)
            for attempt in range(1, attempts + 1):
                start = time.monotonic()
                exit_code, stdout, stderr = runner(pinned, code_dir, probe_source, timeout)
                duration_ms = round((time.monotonic() - start) * 1000)
                stem = f"{revision}-{attempt}"
                (log_dir / f"{stem}.stdout").write_bytes(stdout)
                (log_dir / f"{stem}.stderr").write_bytes(stderr)
                executions.append(
                    Execution(
                        revision=revision,
                        commit_sha=sha,
                        attempt=attempt,
                        command_argv=list(config.test_command),
                        exit_code=exit_code,
                        outcome=outcome_for_config(exit_code, config, stdout, stderr),
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
