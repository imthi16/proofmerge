"""LangGraph orchestration of the verification stages in pipeline.py.

The graph schedules work; it never judges it. Every node runs an existing stage
function, routing only skips stages whose inputs are missing, and the verdict comes
from the deterministic_decide stage, which calls verdict.decide().

What the graph adds over the legacy loop:
- explicit, inspectable routing (nodes taken are recorded in proof.json);
- bounded retries of transient model failures (HTTP 429/5xx, no connection) in the
  four model-only nodes, within the same MetaClient call budget. A failure that
  outlasts the retries, or is not transient, becomes a REVIEW reason as before.
- no retry of nodes that run the sandbox: a probe's recorded attempts are evidence.
"""

from collections.abc import Callable
from importlib.metadata import version

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from proofmerge.evidence import OrchestrationRecord, Proof
from proofmerge.meta import MetaError
from proofmerge.pipeline import MODEL_STAGES, STAGES, Deps, Stage, Update, VerifyState

MAX_MODEL_ATTEMPTS = 3  # the first call plus at most two retries
RETRY_INTERVAL_SECONDS = 1.0


def _is_transient(error: Exception) -> bool:
    return isinstance(error, MetaError) and error.transient


def _node(name: str, stage: Stage, deps: Deps) -> Callable[[VerifyState], Update]:
    attempts = 0

    def run(state: VerifyState) -> Update:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            deps.orchestration.nodes.append(name)
        if name not in MODEL_STAGES:
            return stage(state, deps)
        try:
            # A stage re-raises a transient failure only while attempts remain.
            return stage(state, deps, final_attempt=attempts >= MAX_MODEL_ATTEMPTS)
        except MetaError as error:
            deps.orchestration.retries.append(f"{name}: {error}")
            raise

    return run


def _after_intent(state: VerifyState) -> str:
    return "generate_hypotheses" if state["intent_established"] else "cross_check_claims"


def _after_hypotheses(state: VerifyState) -> str:
    return "generate_execute_probes" if state["hypotheses_ready"] else "cross_check_claims"


def _after_probe_check(state: VerifyState) -> str:
    # The tamper guard reads pytest tests; config-driven languages skip it.
    return "run_tamper_guard" if state["config"] is None else "normalize_findings"


def build_graph(deps: Deps, *, retry_interval: float | None = None):
    """Compile the verification graph for one run (nodes close over its Deps)."""
    if retry_interval is None:
        retry_interval = RETRY_INTERVAL_SECONDS
    retry = RetryPolicy(
        max_attempts=MAX_MODEL_ATTEMPTS,
        initial_interval=retry_interval,
        backoff_factor=2.0,
        max_interval=retry_interval * 4,
        jitter=retry_interval > 0,
        retry_on=_is_transient,
    )
    builder = StateGraph(VerifyState)
    for name, stage in STAGES:
        builder.add_node(
            name, _node(name, stage, deps), retry_policy=retry if name in MODEL_STAGES else None
        )
    builder.add_edge(START, "resolve_context")
    builder.add_edge("resolve_context", "load_trusted_config")
    builder.add_edge("load_trusted_config", "open_evidence_dir")
    builder.add_edge("open_evidence_dir", "extract_intent")
    builder.add_conditional_edges(
        "extract_intent", _after_intent, ["generate_hypotheses", "cross_check_claims"]
    )
    builder.add_conditional_edges(
        "generate_hypotheses", _after_hypotheses, ["generate_execute_probes", "cross_check_claims"]
    )
    builder.add_edge("generate_execute_probes", "cross_check_claims")
    # Sequential on purpose: MetaClient's call budget and order are not thread-safe.
    builder.add_edge("cross_check_claims", "check_probe_relevance")
    builder.add_conditional_edges(
        "check_probe_relevance", _after_probe_check, ["run_tamper_guard", "normalize_findings"]
    )
    builder.add_edge("run_tamper_guard", "normalize_findings")
    builder.add_edge("normalize_findings", "deterministic_decide")
    builder.add_edge("deterministic_decide", "write_evidence")
    builder.add_edge("write_evidence", END)
    return builder.compile()


def run_graph(state: VerifyState, deps: Deps, *, retry_interval: float | None = None) -> Proof:
    deps.orchestration = OrchestrationRecord(
        engine="langgraph", langgraph_version=version("langgraph")
    )
    final = build_graph(deps, retry_interval=retry_interval).invoke(state)
    return final["proof"]
