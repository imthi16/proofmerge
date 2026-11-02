"""Opt-in OpenTelemetry spans and metrics for `proofmerge verify`.

Off unless PROOFMERGE_OTEL=1 and the OpenTelemetry API is installed
(pip install 'proofmerge[otel]'). ProofMerge configures no exporter: spans go wherever
the process's OpenTelemetry SDK sends them, and with no SDK they are no-ops.

Telemetry is a StageObserver plus one root span, so it sees exactly what progress
events see. Every attribute passes through safe_attributes(), an allowlist of run ids,
stage names, counts, durations and the decision. Never recorded: MODEL_API_KEY or any
environment value, PR text, prompts or model replies, probe source, stdout/stderr,
repository source, or session tokens.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from proofmerge.pipeline import StageObserver

SAFE_ATTRIBUTES = frozenset({
    "proofmerge.run_id", "proofmerge.stage", "proofmerge.orchestrator", "proofmerge.decision",
    "proofmerge.language", "proofmerge.probe_count", "proofmerge.model_calls",
    "proofmerge.retry_count", "proofmerge.duration_ms", "proofmerge.errors",
    "proofmerge.hypotheses", "proofmerge.executions", "proofmerge.findings",
    "proofmerge.verified", "proofmerge.tamper", "proofmerge.claims", "proofmerge.skipped",
})
_FROM_DATA = {
    "duration_ms": "proofmerge.duration_ms", "errors": "proofmerge.errors",
    "probes": "proofmerge.probe_count", "hypotheses": "proofmerge.hypotheses",
    "executions": "proofmerge.executions", "findings": "proofmerge.findings",
    "verified": "proofmerge.verified", "tamper": "proofmerge.tamper",
    "claims": "proofmerge.claims", "decision": "proofmerge.decision",
}
_DECISIONS = {"PASS", "REVIEW", "BLOCK"}


def safe_attributes(attributes: dict[str, Any]) -> dict[str, str | int | bool]:
    """Keep allowlisted keys with scalar values; drop everything else."""
    safe: dict[str, str | int | bool] = {}
    for key, value in attributes.items():
        if key not in SAFE_ATTRIBUTES or not isinstance(value, str | int | bool):
            continue
        if key == "proofmerge.decision" and value not in _DECISIONS:
            continue
        if isinstance(value, str) and len(value) > 200:
            continue
        safe[key] = value
    return safe


def enabled() -> bool:
    return os.environ.get("PROOFMERGE_OTEL") == "1"


class TelemetryObserver:
    """Stage spans under one root span, and stage metrics, from observer callbacks."""

    def __init__(self, tracer: Any, root: Any, meter: Any | None = None,
                 set_parent: Any = None) -> None:
        self._tracer, self._root, self._set_parent = tracer, root, set_parent
        self._spans: dict[str, Any] = {}
        self.retries = 0
        self._stage_seconds = self._retry_counter = None
        if meter is not None:
            self._stage_seconds = meter.create_histogram(
                "proofmerge_stage_duration_seconds", unit="s",
                description="Wall time of one verification stage")
            self._retry_counter = meter.create_counter(
                "proofmerge_model_retries_total", description="Transient model retries")

    def _start(self, stage: str) -> Any:
        context = self._set_parent(self._root) if self._set_parent else None
        kwargs = {"context": context} if context is not None else {}
        return self._tracer.start_span(f"proofmerge.stage.{stage}", **kwargs)

    def __call__(self, kind: str, stage: str, data: dict[str, Any]) -> None:
        if kind == "started":
            span = self._start(stage)
            span.set_attributes(safe_attributes({"proofmerge.stage": stage}))
            self._spans[stage] = span
        elif kind == "retrying":
            self.retries += 1
            if self._retry_counter is not None:
                self._retry_counter.add(1, safe_attributes({"proofmerge.stage": stage}))
        elif kind == "skipped":
            span = self._start(stage)
            span.set_attributes(safe_attributes({"proofmerge.stage": stage,
                                                 "proofmerge.skipped": True}))
            span.end()
        elif kind == "completed":
            span = self._spans.pop(stage, None) or self._start(stage)
            attributes = {_FROM_DATA[k]: v for k, v in data.items() if k in _FROM_DATA}
            span.set_attributes(safe_attributes(attributes | {"proofmerge.stage": stage}))
            span.end()
            if self._stage_seconds is not None and isinstance(data.get("duration_ms"), int):
                self._stage_seconds.record(data["duration_ms"] / 1000,
                                           safe_attributes({"proofmerge.stage": stage}))


@contextmanager
def verify_span(run_id: str, orchestrator: str) -> Iterator["VerifyTelemetry | None"]:
    """A root span for one verify, or None when telemetry is off or not installed."""
    if not enabled():
        yield None
        return
    try:
        from opentelemetry import metrics, trace
    except ImportError:
        yield None
        return
    tracer = trace.get_tracer("proofmerge")
    meter = metrics.get_meter("proofmerge")
    root = tracer.start_span("proofmerge.verify")
    root.set_attributes(safe_attributes({"proofmerge.run_id": run_id,
                                         "proofmerge.orchestrator": orchestrator}))
    telemetry = VerifyTelemetry(TelemetryObserver(tracer, root, meter, trace.set_span_in_context),
                                root, meter)
    try:
        yield telemetry
    finally:
        root.end()


class VerifyTelemetry:
    def __init__(self, observer: TelemetryObserver, root: Any, meter: Any) -> None:
        self.observer: StageObserver = observer
        self._root, self._meter = root, meter

    def finish(self, decision: str, model_calls: int, language: str) -> None:
        self._root.set_attributes(safe_attributes({
            "proofmerge.decision": decision, "proofmerge.model_calls": model_calls,
            "proofmerge.language": language,
            "proofmerge.retry_count": getattr(self.observer, "retries", 0),
        }))
        self._meter.create_counter(
            "proofmerge_verifications_total", description="Finished verifications"
        ).add(1, safe_attributes({"proofmerge.decision": decision}))


def fan_out(*observers: StageObserver | None) -> StageObserver | None:
    """One observer that calls each given one; None when there are none."""
    active = [o for o in observers if o is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def observe(kind: str, stage: str, data: dict[str, Any]) -> None:
        for observer in active:
            try:
                observer(kind, stage, data)
            except Exception:  # noqa: BLE001, S110 - one failing observer must not stop the rest
                pass

    return observe
