"""Response models for the dashboard's /api/v1 (the Control Center).

These wrap the evidence contract; they never replace it. `RunDetail.proof` is the
exact `Proof` from proof.json and its `verdict` is the one `verdict.decide()` wrote.
Nothing here computes, scores or overrides a decision: the UI only displays one.

Everything a client sees is listed here, so the TypeScript types in ui/ are generated
from these models (scripts/export_api_schema.py) instead of being kept by hand.
"""

from typing import Literal

from pydantic import BaseModel, Field, StrictInt

from proofmerge.evidence import Decision, Outcome, Proof

API_VERSION = 1
IntegrityStatus = Literal["intact", "changed", "unsealed"]
JobStatus = Literal["queued", "running", "done", "failed"]
EventType = Literal[
    "job.created",
    "job.fetch_started",
    "job.fetch_completed",
    "stage.started",
    "stage.retrying",
    "stage.completed",
    "stage.skipped",
    "job.verdict_ready",
    "job.evidence_written",
    "job.completed",
    "job.failed",
]
StageStatus = Literal["started", "retrying", "completed", "skipped"]


class Integrity(BaseModel):
    status: IntegrityStatus
    problems: list[str]


class BenchmarkLabel(BaseModel):
    label: str
    held_out: bool


class RunSummary(BaseModel):
    """One row of the run list. `decision` is copied from proof.json, never derived."""

    id: str  # folder relative to the dashboard root; a lookup key, never a path
    run_id: str | None = None
    proof_path: str | None = None
    decision: Decision | None = None  # None only when proof.json could not be read
    reasons: list[str] = []
    base_sha: str | None = None
    head_sha: str | None = None
    hypotheses: int = 0
    regressions: int = 0
    tamper: int = 0
    calls: int = 0
    tokens: int = 0
    endpoint: str | None = None
    models: dict[str, str] = {}
    orchestrator: str | None = None
    integrity: Integrity | None = None
    benchmark: BenchmarkLabel | None = None
    # A benchmark comparison, not a verdict: a BLOCK-labelled candidate that was not
    # blocked, or a safe one that was.
    label_disagrees: bool = False
    has_report: bool = False
    modified: float | None = None  # proof.json mtime, seconds since the epoch
    error: str | None = None


class DecisionCounts(BaseModel):
    PASS: int = 0
    REVIEW: int = 0
    BLOCK: int = 0
    unreadable: int = 0


class RunPage(BaseModel):
    root: str
    total: int  # runs matching the filters, before paging
    offset: int
    limit: int
    counts: DecisionCounts  # every run that matches the filters except the decision filter
    run_sets: list[str]  # top-level folders that hold runs, for grouping
    runs: list[RunSummary]


class ExecutionLog(BaseModel):
    outcome: Outcome
    exit_code: int | None
    duration_ms: int
    command: list[str]
    stdout: str  # the last MAX_LOG characters
    stderr: str


class RunDetail(BaseModel):
    summary: RunSummary
    proof: Proof
    probes: dict[str, str | None]  # hypothesis id -> probe source
    failures: dict[str, list[str]]  # hypothesis id -> pytest "E " lines from a head FAIL
    logs: dict[str, ExecutionLog]  # "<hypothesis>:<revision>:<attempt>"
    replay_command: str


class BenchmarkCandidate(BaseModel):
    name: str
    label: str
    held_out: bool
    decision: str | None = None
    reasons: list[str] = []
    calls: int = 0
    a: str | None = None  # baseline A decision
    b: str | None = None  # baseline B decision
    b_reason: str | None = None


class BenchmarkSet(BaseModel):
    id: str
    run_prefix: str = ""  # a candidate's run id is run_prefix + its name
    kind: Literal["run", "baselines"]
    git_sha: str | None = None
    date: str | None = None
    model: str | None = None
    candidates: list[BenchmarkCandidate]


class Benchmarks(BaseModel):
    benchmarks: list[BenchmarkSet]
    known_runs: list[str]  # run ids under the root, so a candidate can link to its run


class Job(BaseModel):
    """The one verification this dashboard is running or ran last."""

    job_id: str
    run_id: str
    pr_number: int
    status: JobStatus
    step: str
    exit_code: int | None = None
    output: str = ""
    head_sha: str | None = None
    base_branch: str | None = None
    created: float
    last_event_id: int = 0


class PullRequest(BaseModel):
    number: int
    title: str
    author: str
    head: str
    base: str
    head_sha: str
    state: str
    draft: bool
    url: str
    updated: str
    body: str
    runs: list[str]  # runs whose head commit is the PR's head now
    older_runs: list[str]  # pr-<n>-* runs from earlier commits


class PullRequests(BaseModel):
    prs: list[PullRequest]
    job: Job | None = None


class VerificationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    number: StrictInt = Field(gt=0)  # "7" is refused, as in the legacy /api/verify


class VerificationEvent(BaseModel):
    """One step of a verification. Metadata only: no source, prompts, logs or secrets."""

    version: Literal[1] = 1
    event_id: int
    job_id: str
    run_id: str
    timestamp: str  # ISO 8601, UTC
    type: EventType
    stage: str | None = None
    status: StageStatus | JobStatus | None = None
    message: str = ""
    data: dict[str, int | str | bool | None] = {}


class JobEvents(BaseModel):
    job: Job
    events: list[VerificationEvent]
    stages: list[str]  # the pipeline's stage order, so the client never hardcodes it
    model_stages: list[str]


class Check(BaseModel):
    name: str
    ok: bool | None  # None: not applicable or not checked
    detail: str


class Diagnostics(BaseModel):
    version: str
    python: str
    checks: list[Check]
    default_orchestrator: str
    runner_image: str
    evidence_root: str
    repo_configured: bool


class OverviewMetrics(BaseModel):
    root: str
    total_runs: int
    counts: DecisionCounts
    integrity: dict[str, int]  # intact / changed / unsealed -> number of runs
    model_calls: int
    tokens: int
    benchmark_sets: int
    recent: list[RunSummary]  # newest first by proof.json time
    job: Job | None = None
    prs_enabled: bool


class Session(BaseModel):
    api_version: int = API_VERSION
    token: str  # sent back as X-ProofMerge-Token on POST; only this page can read it
    prs: bool
    stages: list[str]
    model_stages: list[str]


class ApiError(BaseModel):
    error: str


API_MODELS: tuple[type[BaseModel], ...] = (
    Session, OverviewMetrics, RunPage, RunDetail, Benchmarks, PullRequests,
    VerificationRequest, Job, JobEvents, VerificationEvent, Diagnostics, ApiError,
)


def api_schema() -> dict:
    """One JSON Schema naming every API model, for generating the client's types."""
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema([(m, "serialization") for m in API_MODELS],
                                   ref_template="#/$defs/{model}")
    defs = schema["$defs"]
    for definition in defs.values():
        # model_dump_json writes every field, defaults included, so all are present.
        if "properties" in definition:
            definition["required"] = sorted(definition["properties"])
    return {
        "title": "ProofMergeApi",
        "type": "object",
        "properties": {m.__name__: {"$ref": f"#/$defs/{m.__name__}"} for m in API_MODELS},
        "required": [m.__name__ for m in API_MODELS],
        "additionalProperties": False,
        "$defs": dict(sorted(defs.items())),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(api_schema(), indent=2, sort_keys=True))
