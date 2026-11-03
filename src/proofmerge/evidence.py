"""The proof.json contract: everything one run observed, in one validated document.

The runner, verdict engine and report all read and write this shape, so it is the
only interface between them. Hashes detect a file changed after capture; they are
not signatures and do not prove who produced the evidence.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from proofmerge.hypotheses import HypothesisSet
from proofmerge.intent import IntentContract

Outcome = Literal["PASS", "FAIL", "ERROR", "TIMEOUT"]
Decision = Literal["PASS", "REVIEW", "BLOCK"]
# Base/head pairs from the spec's differential-result table.
Classification = Literal[
    "REGRESSION",  # base PASS, head FAIL, every run
    "POSSIBLE_FIX",  # base FAIL, head PASS
    "EXISTING_FAILURE",  # FAIL on both
    "NO_DIFFERENCE",  # PASS on both
    "INCONCLUSIVE",  # an error, timeout, or runs that disagree
    "ENVIRONMENT",  # import or dependency install failed; not a behavioral result
]
TamperKind = Literal["DELETED", "SKIPPED", "XFAIL", "ASSERTION_REMOVED", "MODIFIED"]


class Execution(BaseModel):
    """One run of one probe on one revision."""

    revision: Literal["base", "head"]
    commit_sha: str
    attempt: int  # 1-based; a regression needs every attempt to agree
    command_argv: list[str]
    exit_code: int | None  # None when the run was killed by the timeout
    outcome: Outcome
    duration_ms: int
    stdout_path: str  # relative to the run's evidence directory
    stderr_path: str
    runner_image_digest: str | None = None  # None only in the hand-made sample proofs
    # True when the run died importing a module or the dependency install failed.
    # That is an environment result, never PASS and never a regression.
    setup_failure: bool = False


class Experiment(BaseModel):
    """A Spark-written probe for one hypothesis, and every run of it."""

    hypothesis_id: str
    test_source_path: str  # relative to the run's evidence directory
    test_source_sha256: str
    timeout_seconds: int
    executions: list[Execution]


class Finding(BaseModel):
    hypothesis_id: str
    claim_id: str
    classification: Classification
    verified: bool  # True only for a REGRESSION the probe-validity rules accept
    reproducer_command: str


class TamperFinding(BaseModel):
    test_path: str
    test_name: str | None  # None when the whole file is affected
    kind: TamperKind
    base_test_on_head_outcome: Outcome | None  # None until the dynamic check runs
    # The T experiment the outcome came from; None when no base test ran (and in older
    # proofs). replay recomputes the outcome from that experiment's executions.
    experiment_id: str | None = None


class ModelUsage(BaseModel):
    calls: int
    prompt_tokens: int
    completion_tokens: int
    # Older proofs predate these fields; they ran on the Meta API with Muse models.
    endpoint: str | None = None
    models: dict[str, str] = {}  # role ("spark", "wonder") -> model id, "" = the CLI default
    contributor_allowed: bool = False


class OrchestrationRecord(BaseModel):
    """How the stages were scheduled. The verdict policy never reads this."""

    engine: Literal["legacy", "langgraph"]
    langgraph_version: str | None = None
    nodes: list[str] = []  # graph nodes in execution order; empty for the legacy engine
    retries: list[str] = []  # "node: message" for each transient model failure retried


class VerdictInputs(BaseModel):
    """The verdict policy's inputs that are not already evidence elsewhere in the proof.

    With these, `proofmerge replay` recomputes the verdict from the proof alone.
    """

    errors: list[str]  # stage failures, each a REVIEW reason
    intent_established: bool  # intent extraction succeeded
    intent_given: bool  # a non-blank PR description was supplied
    wonder_checked: bool  # Wonder's probe check succeeded on a base-passing probe
    tamper_guard_language: str | None = None  # set when the guard could not run (config runner)
    # What a trusted [python] layout did not cover (python_layout.scope_notes), from git.
    layout_scope_limitations: list[str] = []
    # Changed files Spark was not shown (diffbudget.view_diff): source files, which block a PASS, and
    # test, lock, generated or binary files, which are only noted.
    unshown_files: list[str] = []
    unshown_other: list[str] = []


class Verdict(BaseModel):
    decision: Decision
    reasons: list[str]  # one plain sentence per rule that fired
    unresolved_risks: list[str]
    scope_limitations: list[str]


class Proof(BaseModel):
    proofmerge_version: str
    run_id: str
    base_sha: str
    head_sha: str
    diff_sha256: str
    language: str = "python"  # default keeps older proofs valid
    intent: IntentContract
    hypotheses: HypothesisSet
    experiments: list[Experiment]
    findings: list[Finding]
    tamper_findings: list[TamperFinding]
    verdict: Verdict
    model_usage: ModelUsage
    contested_claim_ids: list[str] = []
    mismatched_probe_ids: list[str] = []
    explained_probe_ids: list[str] = []
    explained_probe_claim_ids: dict[str, str] = {}
    orchestration: OrchestrationRecord | None = None  # None in proofs older than T55
    verdict_inputs: VerdictInputs | None = None  # None in proofs older than T56


def read_evidence(evidence_dir: Path, relative: str) -> str | None:
    """Text of a file the proof names, or None when the path leaves the evidence directory.

    proof.json is untrusted: an absolute path, `..` or a symlink must never read a host file."""
    root = evidence_dir.resolve()
    path = (evidence_dir / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")
