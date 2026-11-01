"""Data for the Control Center's /api/v1, as validated dashboard_models.

dashboard.py owns HTTP and the trust boundary (Host, Origin, token, run-id lookup);
this module only reads evidence and job state and shapes it. Every decision shown is
`proof.verdict.decision` as written by verdict.decide(); nothing here derives one.
"""

import importlib.util
import os
import platform
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from proofmerge import __version__
from proofmerge import dashboard as core
from proofmerge.dashboard_models import (
    Benchmarks,
    BenchmarkSet,
    Check,
    DecisionCounts,
    Diagnostics,
    Job,
    JobEvents,
    OverviewMetrics,
    PullRequest,
    PullRequests,
    RunDetail,
    RunPage,
    RunSummary,
    Session,
)
from proofmerge.dashboard_prs import PullRequests as PrSource
from proofmerge.evidence import Proof
from proofmerge.pipeline import MODEL_STAGES, STAGES
from proofmerge.sandbox import RUNNER_IMAGE

MAX_PAGE = 500
_ORDER = {"BLOCK": 0, "REVIEW": 1, "PASS": 2, None: 3}
SORTS: dict[str, Callable[[RunSummary], Any]] = {
    "verdict": lambda r: (_ORDER[r.decision], r.id),
    "recent": lambda r: (-(r.modified or 0), r.id),
    "name": lambda r: r.id,
    "regressions": lambda r: (-r.regressions, r.id),
    "calls": lambda r: (-r.calls, r.id),
}
STAGE_NAMES = [name for name, _ in STAGES]
MODEL_STAGE_NAMES = [name for name in STAGE_NAMES if name in MODEL_STAGES]


def session(token: str, prs: PrSource | None) -> Session:
    return Session(token=token, prs=prs is not None, stages=STAGE_NAMES,
                   model_stages=MODEL_STAGE_NAMES)


def summaries(root: Path) -> list[RunSummary]:
    return [RunSummary.model_validate(core._summary(k, v)) for k, v in core.scan(root).items()]


def _counts(rows: list[RunSummary]) -> DecisionCounts:
    counts = DecisionCounts()
    for row in rows:
        if row.decision is None:
            counts.unreadable += 1
        else:
            setattr(counts, row.decision, getattr(counts, row.decision) + 1)
    return counts


def _one(query: dict[str, list[str]], name: str, default: str = "") -> str:
    return (query.get(name) or [default])[0]


def _int(query: dict[str, list[str]], name: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(_one(query, name, str(default)))))
    except ValueError:
        return default


def runs_page(root: Path, query: dict[str, list[str]]) -> RunPage:
    """Filter, sort and page run summaries. The decision filter does not change counts."""
    rows = summaries(root)
    run_sets = sorted({r.id.split("/")[0] for r in rows if "/" in r.id}, reverse=True)
    text = _one(query, "q").strip().lower()
    integrity = _one(query, "integrity")
    run_set = _one(query, "set")
    disagree = _one(query, "disagrees") == "1"
    decision = _one(query, "decision")

    def keep(row: RunSummary) -> bool:
        if integrity and (row.integrity is None or row.integrity.status != integrity):
            return False
        if run_set and not row.id.startswith(run_set + "/"):
            return False
        if disagree and not row.label_disagrees:
            return False
        haystack = " ".join([row.id, row.run_id or "", *row.reasons]).lower()
        return not text or text in haystack

    matching = [r for r in rows if keep(r)]
    counts = _counts(matching)
    if decision == "ERROR":
        matching = [r for r in matching if r.decision is None]
    elif decision:
        matching = [r for r in matching if r.decision == decision]
    matching.sort(key=SORTS.get(_one(query, "sort", "verdict"), SORTS["verdict"]))
    offset = _int(query, "offset", 0, 0, 10**6)
    limit = _int(query, "limit", 50, 1, MAX_PAGE)
    return RunPage(root=root.name, total=len(matching), offset=offset, limit=limit,
                   counts=counts, run_sets=run_sets, runs=matching[offset:offset + limit])


def run_detail(run_id: str, run_dir: Path) -> RunDetail:
    detail = core._detail(run_id, run_dir)
    summary = RunSummary.model_validate(detail["summary"])
    replay = f"proofmerge replay {shlex.quote(summary.proof_path or str(run_dir / 'proof.json'))}"
    return RunDetail(
        summary=summary, proof=Proof.model_validate(detail["proof"]), probes=detail["probes"],
        failures=detail["failures"], logs=detail["logs"], replay_command=replay,
    )


def benchmarks(root: Path) -> Benchmarks:
    return Benchmarks(benchmarks=[BenchmarkSet.model_validate(b) for b in core._benchmarks(root)],
                      known_runs=sorted(core.scan(root)))


def job_model(prs: PrSource | None) -> Job | None:
    if prs is None or prs.job is None or "job_id" not in prs.job:
        return None
    job = prs.job
    journal = prs.journal(job["job_id"])
    return Job(
        job_id=job["job_id"], run_id=job["run"], pr_number=job["number"], status=job["status"],
        step=job["step"], exit_code=job["exit_code"], output=job.get("output", ""),
        head_sha=job.get("head_sha"), base_branch=job.get("base_branch"),
        created=job.get("created", 0.0), last_event_id=journal.last_id if journal else 0,
    )


def job_events(prs: PrSource | None, job_id: str, last_id: int = 0) -> JobEvents | None:
    if prs is None:
        return None
    journal = prs.journal(job_id)
    job = job_model(prs)
    if journal is None or job is None:
        return None
    if job.job_id != job_id:
        # An older job: only its events are still in memory.
        events = journal.since(0)
        terminal = next((e for e in reversed(events) if e.type in ("job.completed", "job.failed")), None)
        job = Job(job_id=job_id, run_id=journal.run_id, pr_number=int(events[0].data.get("pr_number") or 0)
                  if events else 0, status=terminal.status if terminal else "failed",  # type: ignore[arg-type]
                  step=terminal.message if terminal else "Interrupted", created=0.0,
                  last_event_id=journal.last_id)
    return JobEvents(job=job, events=journal.since(last_id), stages=STAGE_NAMES,
                     model_stages=MODEL_STAGE_NAMES)


def pull_requests(root: Path, prs: PrSource, refresh: bool) -> PullRequests:
    pulls = prs.pulls(refresh=refresh)
    runs = core.scan(root)
    by_head = core._runs_by_head(runs)
    rows = []
    for pr in pulls:
        current = by_head.get(pr["head_sha"], [])
        prefix = f"pr-{pr['number']}-"
        older = [i for i in runs if i.rsplit("/", 1)[-1].startswith(prefix) and i not in current]
        rows.append(PullRequest.model_validate(pr | {"runs": current, "older_runs": older}))
    return PullRequests(prs=rows, job=job_model(prs))


def overview(root: Path, prs: PrSource | None) -> OverviewMetrics:
    rows = summaries(root)
    integrity = {"intact": 0, "changed": 0, "unsealed": 0}
    for row in rows:
        if row.integrity is not None:
            integrity[row.integrity.status] += 1
    recent = sorted((r for r in rows if r.decision), key=SORTS["recent"])[:6]
    return OverviewMetrics(
        root=root.name, total_runs=len(rows), counts=_counts(rows), integrity=integrity,
        model_calls=sum(r.calls for r in rows), tokens=sum(r.tokens for r in rows),
        benchmark_sets=len(core._benchmarks(root)), recent=recent, job=job_model(prs),
        prs_enabled=prs is not None,
    )


Runner = Callable[..., Any]


def _quiet(runner: Runner, argv: list[str]) -> Any:
    """Run a fixed status command; its output is never shown, only whether it worked."""
    try:
        return runner(argv, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def diagnostics(root: Path, prs: PrSource | None, runner: Runner = subprocess.run) -> Diagnostics:
    """Readiness checks. Presence and status only: never a credential or command output."""
    checks = []
    docker = shutil.which("docker")
    daemon = _quiet(runner, ["docker", "version", "--format", "{{.Server.Version}}"]) if docker else None
    daemon_ok = daemon is not None and daemon.returncode == 0
    checks.append(Check(name="Docker daemon", ok=daemon_ok,
                        detail="reachable" if daemon_ok else
                        "not installed" if not docker else "not reachable (is Docker running?)"))
    image = _quiet(runner, ["docker", "image", "inspect", "--format", "{{.Id}}", RUNNER_IMAGE]) \
        if daemon_ok else None
    image_ok = image is not None and image.returncode == 0
    checks.append(Check(name="Runner image", ok=image_ok if daemon_ok else None,
                        detail=f"{RUNNER_IMAGE} present" if image_ok else
                        f"{RUNNER_IMAGE} missing: run `proofmerge setup`" if daemon_ok else
                        "needs Docker"))
    gh = shutil.which("gh")
    auth = _quiet(runner, ["gh", "auth", "status"]) if gh else None
    auth_ok = auth is not None and auth.returncode == 0
    checks.append(Check(name="GitHub CLI", ok=auth_ok if gh else (False if prs else None),
                        detail="installed and logged in" if auth_ok else
                        "installed, not logged in: run `gh auth login`" if gh else "not installed"))
    backend = os.environ.get("PROOFMERGE_BACKEND", "api")
    if backend == "api":
        has_key = bool(os.environ.get("MODEL_API_KEY"))
        checks.append(Check(name="Model API key", ok=has_key,
                            detail="MODEL_API_KEY is set" if has_key else "MODEL_API_KEY is not set"))
    else:
        checks.append(Check(name="Model API key", ok=None,
                            detail="not needed: PROOFMERGE_BACKEND is not api"))
    graph = importlib.util.find_spec("langgraph") is not None
    checks.append(Check(name="LangGraph orchestrator", ok=graph,
                        detail="installed" if graph else "not installed: pip install 'proofmerge[graph]'"))
    otel = os.environ.get("PROOFMERGE_OTEL") == "1"
    otel_ok = importlib.util.find_spec("opentelemetry") is not None
    checks.append(Check(name="OpenTelemetry", ok=(otel_ok if otel else None),
                        detail=("on" if otel_ok else "on, but not installed") if otel else
                        "off (set PROOFMERGE_OTEL=1 to enable)"))
    default = os.environ.get("PROOFMERGE_ORCHESTRATOR") or "legacy"
    return Diagnostics(
        version=__version__, python=platform.python_version(), checks=checks,
        default_orchestrator=default if default in ("legacy", "langgraph") else "invalid",
        runner_image=RUNNER_IMAGE, evidence_root=root.name, repo_configured=prs is not None,
    )
