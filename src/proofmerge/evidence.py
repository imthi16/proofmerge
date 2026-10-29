"""The proof.json contract: everything one run observed, in one validated document.

The runner, verdict engine and report all read and write this shape, so it is the
only interface between them. Hashes detect a file changed after capture; they are
not signatures and do not prove who produced the evidence.
"""

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


class ModelUsage(BaseModel):
    calls: int
    prompt_tokens: int
    completion_tokens: int


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
    intent: IntentContract
    hypotheses: HypothesisSet
    experiments: list[Experiment]
    findings: list[Finding]
    tamper_findings: list[TamperFinding]
    verdict: Verdict
    model_usage: ModelUsage
    contested_claim_ids: list[str] = []
    mismatched_probe_ids: list[str] = []
