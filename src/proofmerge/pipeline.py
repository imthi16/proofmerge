"""Wire verification stages into a recorded, replayable evidence bundle.

A run is a fixed sequence of stages over one VerifyState. Each stage reads the state
and returns only the fields it changed; "errors" accumulates. Two orchestrators run
the same stage functions: "legacy" calls them in order, "langgraph" (graph.py) runs
them as graph nodes with explicit routing and bounded retries of transient model
failures. Only deterministic_decide produces a verdict, and only via verdict.decide().
"""

import operator
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from proofmerge import __version__
from proofmerge.context import ContextError, GitContext, get_context
from proofmerge.evidence import (
    Experiment,
    Finding,
    ModelUsage,
    OrchestrationRecord,
    Proof,
    TamperFinding,
    Verdict,
)
from proofmerge.findings import findings_for
from proofmerge.hypotheses import HypothesisSet, check_diff, generate_hypotheses
from proofmerge.intent import C0, IntentContract, extract_intent
from proofmerge.manifest import write_manifest
from proofmerge.meta import SPARK_MODEL, WONDER_MODEL, MetaClient, MetaError
from proofmerge.probes import Probe, probe_hypotheses
from proofmerge.project_config import ProjectConfig, load_config
from proofmerge.report import render_html
from proofmerge.sandbox import Runner, SandboxError, docker_runner
from proofmerge.tamper import TamperError, find_tamper, run_base_tests_on_head
from proofmerge.verdict import decide
from proofmerge.wonder import check_probes, cross_check_claims

ORCHESTRATORS = ("legacy", "langgraph")


class VerifyState(TypedDict, total=False):
    """Everything later stages read. Run dependencies (client, runner) live in Deps."""

    repo: Path
    base: str
    head: str
    intent: str | None
    evidence_dir: Path
    config: ProjectConfig | None
    context: GitContext
    contract: IntentContract
    intent_established: bool
    found: HypothesisSet
    hypotheses_ready: bool
    probes: list[Probe]
    experiments: list[Experiment]
    contested_claim_ids: frozenset[str]
    mismatched_probe_ids: frozenset[str]
    explained_probe_ids: frozenset[str]
    explained_probe_claim_ids: dict[str, str]
    wonder_checked: bool
    tamper: list[TamperFinding]
    tamper_experiments: list[Experiment]
    findings: list[Finding]
    verdict: Verdict
    proof: Proof
    errors: Annotated[list[str], operator.add]


@dataclass
class Deps:
    """Per-run dependencies, kept out of the state so the state stays plain data."""

    client: MetaClient
    runner: Runner
    digest: str | None
    orchestration: OrchestrationRecord = field(
        default_factory=lambda: OrchestrationRecord(engine="legacy")
    )


Update = dict[str, Any]
Stage = Callable[..., Update]


def _model_failure(error: MetaError, final_attempt: bool) -> Update:
    """Record a model failure, or re-raise a transient one so the graph can retry it."""
    if error.transient and not final_attempt:
        raise error
    return {"errors": [str(error)]}


def resolve_context(state: VerifyState, deps: Deps) -> Update:
    context = get_context(state["repo"], state["base"], state["head"])
    check_diff(context)
    return {"context": context}


def load_trusted_config(state: VerifyState, deps: Deps) -> Update:
    # From the base commit only: the PR head must not choose how it is tested.
    config = state.get("config")
    if config is None:
        config = load_config(state["repo"], state["context"].base_sha)
    return {"config": config}


def open_evidence_dir(state: VerifyState, deps: Deps) -> Update:
    state["evidence_dir"].mkdir(parents=True)  # fails on an existing dir
    return {}


def extract_intent_stage(state: VerifyState, deps: Deps, *, final_attempt: bool = True) -> Update:
    try:
        contract = extract_intent(state["intent"], deps.client)
    except MetaError as error:
        return _model_failure(error, final_attempt)
    except (OSError, ValueError) as error:
        return {"errors": [str(error)]}
    return {"contract": contract, "intent_established": True}


def generate_hypotheses_stage(
    state: VerifyState, deps: Deps, *, final_attempt: bool = True
) -> Update:
    if not state["intent_established"]:
        return {}
    try:
        found = generate_hypotheses(
            state["context"], state["contract"], deps.client, config=state["config"]
        )
    except MetaError as error:
        return _model_failure(error, final_attempt)
    except (SandboxError, OSError, ValueError) as error:
        return {"errors": [str(error)]}
    return {"found": found, "hypotheses_ready": True}


def generate_execute_probes(state: VerifyState, deps: Deps) -> Update:
    # Never retried as a whole: each probe already runs a fixed number of recorded
    # attempts, and repeating them would change what the evidence means.
    if not state["hypotheses_ready"]:
        return {}
    try:
        probes, experiments = probe_hypotheses(
            state["repo"], state["context"], state["contract"], state["found"].hypotheses,
            deps.client, state["evidence_dir"],
            config=state["config"], runner=deps.runner, digest=deps.digest,
        )
    except (MetaError, SandboxError, OSError, ValueError) as error:
        update: Update = {"errors": [str(error)]}
        done = getattr(error, "completed", None)
        if isinstance(done, dict):
            update["probes"] = done.get("probes", state["probes"])
            update["experiments"] = done.get("experiments", state["experiments"])
        return update
    return {"probes": probes, "experiments": experiments}


def cross_check_claims_stage(
    state: VerifyState, deps: Deps, *, final_attempt: bool = True
) -> Update:
    try:
        contested = cross_check_claims(state["contract"], state["intent"], deps.client)
    except MetaError as error:
        return _model_failure(error, final_attempt)
    return {"contested_claim_ids": contested}


def check_probe_relevance(
    state: VerifyState, deps: Deps, *, final_attempt: bool = True
) -> Update:
    probes = state["probes"]
    try:
        mismatched, explained, explained_claims = check_probes(
            state["found"].hypotheses, probes, deps.client,
            contract=state["contract"], intent=state["intent"],
        )
    except MetaError as error:
        return _model_failure(error, final_attempt)
    return {
        "mismatched_probe_ids": mismatched,
        "explained_probe_ids": explained,
        "explained_probe_claim_ids": explained_claims,
        "wonder_checked": any(probe.passed_on_base for probe in probes),
    }


def run_tamper_guard(state: VerifyState, deps: Deps) -> Update:
    if state["config"] is not None:
        return {}  # the guard reads pytest tests only
    repo, context = state["repo"], state["context"]
    tamper, experiments = state["tamper"], state["tamper_experiments"]
    try:
        tamper = find_tamper(repo, context)
        tamper, experiments = run_base_tests_on_head(
            repo, context, tamper, state["evidence_dir"], runner=deps.runner, digest=deps.digest
        )
    except (TamperError, SandboxError, ContextError, OSError, ValueError) as error:
        done = getattr(error, "completed", None)
        if isinstance(done, dict):
            tamper = done.get("tamper", tamper)
            experiments = done.get("experiments", experiments)
        return {"tamper": tamper, "tamper_experiments": experiments, "errors": [str(error)]}
    return {"tamper": tamper, "tamper_experiments": experiments}


def normalize_findings(state: VerifyState, deps: Deps) -> Update:
    findings = findings_for(
        state["experiments"], state["found"].hypotheses, state["probes"],
        proof_path=str(state["evidence_dir"] / "proof.json"),
    )
    return {"findings": findings}


def deterministic_decide(state: VerifyState, deps: Deps) -> Update:
    intent = state["intent"]
    # Stage errors only add REVIEW reasons: hard evidence already gathered can still BLOCK.
    verdict = decide(
        state["contract"], state["found"].hypotheses, state["findings"], state["tamper"],
        contested_claim_ids=state["contested_claim_ids"],
        mismatched_probe_ids=state["mismatched_probe_ids"],
        errors=list(state["errors"]), intent_established=state["intent_established"],
        intent_given=bool(intent and intent.strip()),
        explained_probe_ids=state["explained_probe_ids"],
        explained_probe_claim_ids=state["explained_probe_claim_ids"],
        wonder_checked=state["wonder_checked"],
    )
    config = state["config"]
    if config is not None:
        verdict.scope_limitations.append(
            f"Test-tamper guard is not available for {config.language}; "
            "changed tests were not re-run."
        )
    return {"verdict": verdict}


def write_evidence(state: VerifyState, deps: Deps) -> Update:
    """Seal the bundle last: proof.json, report.html, then the hashed manifest."""
    client, context, config = deps.client, state["context"], state["config"]
    evidence_dir = state["evidence_dir"]
    usage = [u for u in client.usage if u is not None]
    proof = Proof(
        proofmerge_version=__version__,
        run_id=evidence_dir.name,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        diff_sha256=context.diff_sha256,
        language=config.language if config is not None else "python",
        intent=state["contract"],
        hypotheses=state["found"],
        experiments=state["experiments"] + state["tamper_experiments"],
        findings=state["findings"],
        tamper_findings=state["tamper"],
        verdict=state["verdict"],
        contested_claim_ids=sorted(state["contested_claim_ids"]),
        mismatched_probe_ids=sorted(state["mismatched_probe_ids"]),
        explained_probe_ids=sorted(state["explained_probe_ids"]),
        explained_probe_claim_ids=state["explained_probe_claim_ids"],
        model_usage=ModelUsage(
            calls=client.calls,
            prompt_tokens=sum(u.prompt_tokens for u in usage),
            completion_tokens=sum(u.completion_tokens for u in usage),
            endpoint=client.endpoint,
            models={"spark": client.models[SPARK_MODEL], "wonder": client.models[WONDER_MODEL]},
            contributor_allowed=client.allow_contributor,
        ),
        orchestration=deps.orchestration.model_copy(deep=True),
    )
    (evidence_dir / "proof.json").write_text(proof.model_dump_json(indent=2), encoding="utf-8")
    (evidence_dir / "report.html").write_text(render_html(proof, evidence_dir), encoding="utf-8")
    write_manifest(evidence_dir)
    return {"proof": proof}


# Execution order. Model-only stages accept final_attempt; the graph may retry them.
STAGES: tuple[tuple[str, Stage], ...] = (
    ("resolve_context", resolve_context),
    ("load_trusted_config", load_trusted_config),
    ("open_evidence_dir", open_evidence_dir),
    ("extract_intent", extract_intent_stage),
    ("generate_hypotheses", generate_hypotheses_stage),
    ("generate_execute_probes", generate_execute_probes),
    ("cross_check_claims", cross_check_claims_stage),
    ("check_probe_relevance", check_probe_relevance),
    ("run_tamper_guard", run_tamper_guard),
    ("normalize_findings", normalize_findings),
    ("deterministic_decide", deterministic_decide),
    ("write_evidence", write_evidence),
)
MODEL_STAGES = frozenset(
    {"extract_intent", "generate_hypotheses", "cross_check_claims", "check_probe_relevance"}
)


def initial_state(
    repo: Path, base: str, head: str, intent: str | None, evidence_dir: Path,
    config: ProjectConfig | None,
) -> VerifyState:
    return VerifyState(
        repo=repo, base=base, head=head, intent=intent, evidence_dir=evidence_dir,
        config=config,
        contract=IntentContract(claims=[C0], rejected=[]),
        intent_established=False,
        found=HypothesisSet(hypotheses=[], rejected=[]),
        hypotheses_ready=False,
        probes=[], experiments=[],
        contested_claim_ids=frozenset(), mismatched_probe_ids=frozenset(),
        explained_probe_ids=frozenset(), explained_probe_claim_ids={},
        wonder_checked=False,
        tamper=[], tamper_experiments=[],
        errors=[],
    )


def resolve_orchestrator(orchestrator: str | None) -> str:
    """The explicit choice, else PROOFMERGE_ORCHESTRATOR, else "legacy"."""
    name = orchestrator or os.environ.get("PROOFMERGE_ORCHESTRATOR") or "legacy"
    if name not in ORCHESTRATORS:
        raise ValueError(f"unknown orchestrator {name!r}; use one of {', '.join(ORCHESTRATORS)}")
    return name


def run_legacy(state: VerifyState, deps: Deps) -> Proof:
    """Call every stage in order; a stage whose inputs are missing returns no update."""
    for _, stage in STAGES:
        update = stage(state, deps)
        errors = update.pop("errors", [])
        state.update(update)  # type: ignore[typeddict-item]
        state["errors"] = state["errors"] + errors
    return state["proof"]


def run_verify(
    repo: Path,
    base: str,
    head: str,
    intent: str | None,
    evidence_dir: Path,
    client: MetaClient,
    *,
    runner: Runner = docker_runner,
    digest: str | None = None,
    config: ProjectConfig | None = None,
    orchestrator: str | None = None,
) -> Proof:
    """Resolve context, collect evidence, adjudicate, then seal the bundle last."""
    engine = resolve_orchestrator(orchestrator)
    state = initial_state(repo, base, head, intent, evidence_dir, config)
    if engine == "langgraph":
        try:
            from proofmerge.graph import run_graph
        except ImportError:
            raise ValueError(
                "the langgraph orchestrator needs LangGraph: pip install 'proofmerge[graph]'"
            ) from None
        return run_graph(state, Deps(client, runner, digest))
    return run_legacy(state, Deps(client, runner, digest))
