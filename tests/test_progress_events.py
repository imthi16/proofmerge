"""Stage progress: observers watch the pipeline and can never change what it proves."""

import json
import threading

import pytest
from test_graph import (
    SCENARIOS,
    Flaky,
    comparable,
    evidence_files,
    http_error,
    verify,
)
from test_pipeline import INTENT, Replies, arguments
from test_probes import fake_runner, make_repo
from typer.testing import CliRunner

from proofmerge import cli, graph, telemetry
from proofmerge.dashboard_events import EventJournal, JsonlEventSink, parse_line, tail_events
from proofmerge.meta import MetaClient
from proofmerge.pipeline import MODEL_STAGES, STAGES, run_verify
from proofmerge.telemetry import TelemetryObserver, fan_out, safe_attributes

STAGE_NAMES = [name for name, _ in STAGES]
SECRET = "sk-SENTINEL-progress-0123456789abcdef"


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(graph, "RETRY_INTERVAL_SECONDS", 0.0)
    monkeypatch.delenv("PROOFMERGE_ORCHESTRATOR", raising=False)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __call__(self, kind, stage, data):
        self.events.append((kind, stage, dict(data)))

    def shape(self):
        # Durations differ run to run; everything else must match across engines.
        return [(k, s, {x: v for x, v in d.items() if x != "duration_ms"}) for k, s, d in self.events]


def observed(tmp_path, engine, spec, observer):
    (tmp_path / engine).mkdir()
    repo, context = make_repo(tmp_path / engine)
    runner = spec["runner"]() if "runner" in spec else fake_runner([])
    client = MetaClient(spec["replies"]())
    evidence = tmp_path / engine / "ev"
    proof = run_verify(
        repo, context.base_sha, context.head_sha, spec.get("intent", INTENT), evidence, client,
        runner=runner, digest="sha256:fake", orchestrator=engine, observer=observer,
    )
    return proof, evidence


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_both_engines_report_the_same_progress(tmp_path, name):
    spec = SCENARIOS[name]
    seen = {}
    for engine in ("legacy", "langgraph"):
        recorder = Recorder()
        proof, _ = observed(tmp_path, engine, spec, recorder)
        seen[engine] = recorder.shape()
        # Every stage is reported once, in pipeline order, as completed or skipped.
        ends = [(s, k) for k, s, _ in recorder.events if k in ("completed", "skipped")]
        assert [s for s, _ in ends] == STAGE_NAMES
        decided = [d for k, s, d in recorder.events if s == "deterministic_decide" and k == "completed"]
        assert decided[0]["decision"] == proof.verdict.decision
    assert seen["legacy"] == seen["langgraph"]


@pytest.mark.parametrize("engine", ["legacy", "langgraph"])
@pytest.mark.parametrize("name", ["block-regression", "review-intent-api-failure", "pass-no-difference"])
def test_an_observer_does_not_change_the_evidence(tmp_path, engine, name):
    spec = SCENARIOS[name]
    repo, context = make_repo(tmp_path)
    results = []
    for label, observer in (("plain", None), ("watched", Recorder())):
        evidence = tmp_path / label / "ev"
        runner = spec["runner"]() if "runner" in spec else None
        client = MetaClient(spec["replies"]())
        proof = run_verify(
            repo, context.base_sha, context.head_sha, spec.get("intent", INTENT), evidence, client,
            runner=runner or fake_runner([]), digest="sha256:fake", orchestrator=engine,
            observer=observer,
        )
        files = evidence_files(evidence)
        record = proof.orchestration.model_dump()
        results.append((comparable(proof, evidence), files, client.calls, record))
    assert results[0] == results[1]


@pytest.mark.parametrize("engine", ["legacy", "langgraph"])
def test_a_failing_observer_cannot_break_a_run(tmp_path, engine):
    def explode(kind, stage, data):
        raise RuntimeError("observer bug")

    repo, context = make_repo(tmp_path)
    expected, _ = verify(repo, context, tmp_path / "a" / "ev", engine)
    proof = run_verify(
        repo, context.base_sha, context.head_sha, INTENT, tmp_path / "b" / "ev",
        MetaClient(Replies()), runner=fake_runner([]), digest="sha256:fake",
        orchestrator=engine, observer=explode,
    )
    assert proof.verdict == expected.verdict == proof.verdict
    assert proof.verdict.decision == "BLOCK"


def test_graph_reports_retries_of_model_stages_only(tmp_path):
    repo, context = make_repo(tmp_path)
    recorder = Recorder()
    run_verify(
        repo, context.base_sha, context.head_sha, INTENT, tmp_path / "ev",
        MetaClient(Flaky({1: http_error(429)})), runner=fake_runner([]), digest="sha256:fake",
        orchestrator="langgraph", observer=recorder,
    )
    retries = [(s, d) for k, s, d in recorder.events if k == "retrying"]
    assert retries == [("extract_intent", {"attempt": 1, "max_attempts": graph.MAX_MODEL_ATTEMPTS})]
    assert all(s in MODEL_STAGES for s, _ in retries)
    starts = [s for k, s, _ in recorder.events if k == "started"]
    assert starts.count("extract_intent") == 1 and starts.count("generate_execute_probes") == 1


@pytest.mark.parametrize("engine", ["legacy", "langgraph"])
def test_no_secret_reaches_events_or_telemetry(tmp_path, monkeypatch, engine):
    monkeypatch.setenv("MODEL_API_KEY", SECRET)
    spans = FakeTracer()
    events_file = tmp_path / "events.jsonl"
    sink = JsonlEventSink(events_file)
    tele = TelemetryObserver(spans, spans.start_span("root"), meter=FakeMeter())
    recorder = Recorder()
    # The model's own replies carry the secret too: progress must not echo model text.
    replies = Replies(wonder_results=[{"hypothesis_id": "H1", "tests_hypothesis": False,
                                       "reason": SECRET}])
    repo, context = make_repo(tmp_path)
    run_verify(
        repo, context.base_sha, context.head_sha, INTENT + " " + SECRET, tmp_path / "ev",
        MetaClient(replies), runner=fake_runner([]), digest="sha256:fake", orchestrator=engine,
        observer=fan_out(sink, tele, recorder),
    )
    sink.close()
    assert SECRET not in events_file.read_text()
    assert SECRET not in json.dumps(recorder.events)
    assert SECRET not in json.dumps(spans.attributes())
    assert spans.names().count("proofmerge.stage.write_evidence") == 1


def test_events_file_round_trip_into_a_journal(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    journal = EventJournal("job", "run")
    stop = threading.Event()
    tailer = threading.Thread(target=tail_events, args=(path, journal, stop, 0.01))
    tailer.start()
    for name in STAGE_NAMES:
        sink("started", name, {})
        sink("completed", name, {"duration_ms": 3} | ({"decision": "PASS"} if name == "deterministic_decide" else {}))
    sink.close()
    stop.set()
    tailer.join(5)
    types = [e.type for e in journal.since(0)]
    assert types.count("stage.completed") == len(STAGE_NAMES)
    assert types.index("job.verdict_ready") == types.index("job.evidence_written") - 3
    assert [e.event_id for e in journal.since(0)] == list(range(1, len(types) + 1))


def test_child_event_lines_are_validated():
    assert parse_line('{"type": "stage.started", "stage": "resolve_context"}') is not None
    assert parse_line('{"type": "stage.started", "stage": "evil"}') is None
    assert parse_line('{"type": "job.completed", "stage": "resolve_context"}') is None
    assert parse_line("{") is None
    event = parse_line(json.dumps({"type": "stage.completed", "stage": "deterministic_decide",
                                   "data": {"decision": "MAYBE", "probes": True, "errors": 2,
                                            "note": "x"}}))
    assert event.data == {"errors": 2}


def test_journal_is_bounded_and_closes_on_the_terminal_event():
    journal = EventJournal("job", "run")
    for _ in range(1000):
        journal.append("stage.started", stage="resolve_context")
    assert journal.append("job.completed", status="done") is not None
    assert journal.closed and journal.append("stage.started") is None
    assert len(journal.since(0)) == 500 and journal.dropped > 0
    assert journal.wait(0, 0.01)


def test_cli_writes_events_outside_the_evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "MetaClient", lambda **k: MetaClient(Replies(), **k))
    monkeypatch.setattr(cli, "run_verify", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    repo, _ = make_repo(tmp_path)
    evidence = tmp_path / "ev"
    inside = CliRunner().invoke(cli.app, arguments(repo, evidence) + ["--events-file", str(evidence / "e.jsonl")])
    assert inside.exit_code == 1 and "outside the evidence directory" in inside.output


def test_cli_events_file_does_not_change_output_or_exit_code(tmp_path, monkeypatch):
    from pathlib import Path

    from test_pipeline import Proof as _Proof  # the fixture proof, as in the CLI exit-code tests

    proof = _Proof.model_validate_json((Path(__file__).parent / "fixtures/sample_proof_block.json").read_text())
    seen = {}

    def run(*args, **kwargs):
        seen.update(kwargs)
        if "observer" in kwargs:
            kwargs["observer"]("started", "resolve_context", {})
        return proof

    monkeypatch.setattr(cli, "MetaClient", lambda **k: MetaClient(Replies(), **k))
    monkeypatch.setattr(cli, "run_verify", run)
    repo, _ = make_repo(tmp_path)
    events = tmp_path / "events.jsonl"
    plain = CliRunner().invoke(cli.app, arguments(repo, tmp_path / "a"))
    monkeypatch.setattr(cli, "run_verify", run)
    watched = CliRunner().invoke(cli.app, arguments(repo, tmp_path / "b") + ["--events-file", str(events)])
    assert plain.exit_code == watched.exit_code == 2
    assert plain.output.replace("/a/", "/b/") == watched.output
    assert json.loads(events.read_text().splitlines()[0])["stage"] == "resolve_context"


# --- Telemetry --------------------------------------------------------------------------

class FakeSpan:
    def __init__(self, name):
        self.name, self.attrs, self.ended = name, {}, False

    def set_attributes(self, attributes):
        self.attrs.update(attributes)

    def end(self):
        self.ended = True


class FakeTracer:
    def __init__(self):
        self.spans: list[FakeSpan] = []

    def start_span(self, name, **kwargs):
        self.spans.append(FakeSpan(name))
        return self.spans[-1]

    def names(self):
        return [s.name for s in self.spans]

    def attributes(self):
        return [s.attrs for s in self.spans]


class FakeInstrument:
    def __init__(self):
        self.values = []

    def add(self, value, attributes=None):
        self.values.append((value, attributes))

    record = add


class FakeMeter:
    def create_histogram(self, *a, **k):
        return FakeInstrument()

    create_counter = create_histogram


def test_safe_attributes_keep_only_the_allowlist():
    kept = safe_attributes({
        "proofmerge.stage": "write_evidence", "proofmerge.decision": "BLOCK",
        "proofmerge.model_calls": 3, "MODEL_API_KEY": SECRET, "proofmerge.prompt": SECRET,
        "proofmerge.run_id": "x" * 500, "proofmerge.errors": ["list"],
    })
    assert kept == {"proofmerge.stage": "write_evidence", "proofmerge.decision": "BLOCK",
                    "proofmerge.model_calls": 3}
    assert safe_attributes({"proofmerge.decision": SECRET}) == {}


def test_telemetry_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PROOFMERGE_OTEL", raising=False)
    with telemetry.verify_span("run", "legacy") as found:
        assert found is None


def test_telemetry_spans_cover_every_stage(tmp_path):
    tracer = FakeTracer()
    observer = TelemetryObserver(tracer, tracer.start_span("proofmerge.verify"), meter=FakeMeter())
    repo, context = make_repo(tmp_path)
    run_verify(repo, context.base_sha, context.head_sha, INTENT, tmp_path / "ev",
               MetaClient(Replies()), runner=fake_runner([]), digest="sha256:fake",
               observer=observer)
    stage_spans = [s for s in tracer.spans if s.name.startswith("proofmerge.stage.")]
    assert [s.name.removeprefix("proofmerge.stage.") for s in stage_spans] == STAGE_NAMES
    assert all(s.ended for s in stage_spans)
    decide = next(s for s in stage_spans if s.name.endswith("deterministic_decide"))
    assert decide.attrs["proofmerge.decision"] == "BLOCK"


def test_telemetry_with_the_real_api_when_enabled(tmp_path, monkeypatch):
    pytest.importorskip("opentelemetry")
    monkeypatch.setenv("PROOFMERGE_OTEL", "1")
    with telemetry.verify_span("run-1", "legacy") as found:
        assert found is not None
        found.observer("started", "resolve_context", {})
        found.observer("completed", "resolve_context", {"duration_ms": 1})
        found.finish("PASS", 0, "python")
