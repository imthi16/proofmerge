"""Verification progress events: from the pipeline, through a file, to the browser.

`proofmerge verify` runs as a child process of the dashboard. With --events-file it
appends one JSON line per stage boundary (JsonlEventSink). The dashboard tails that
file (tail_events), validates every line against a narrow schema, and appends what
passes to the job's bounded EventJournal, which the SSE endpoint streams.

Events are progress metadata only: stage names, counts, durations and, once
deterministic_decide has run, the decision it produced. They never carry probe
source, logs, prompts, model replies, exception text or environment values, and they
are not evidence: proof.json is. The events file lives outside the evidence folder,
so it never enters the manifest.
"""

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator

from proofmerge.dashboard_models import EventType, VerificationEvent
from proofmerge.pipeline import STAGES

MAX_EVENTS = 500  # per job; a verification has ~30, so this only stops a runaway writer
MAX_LINE = 2000  # bytes; longer lines from the events file are dropped
STAGE_NAMES = frozenset(name for name, _ in STAGES)
TERMINAL = frozenset({"job.completed", "job.failed"})
# Values a child event may carry: small counts, a duration, an attempt number, or the
# decision verdict.decide() returned. Anything else is dropped, so a bug can't leak text.
_ALLOWED_DATA = {
    "duration_ms", "errors", "attempt", "max_attempts", "hypotheses", "probes",
    "executions", "tamper", "findings", "verified", "decision", "claims",
}
_DECISIONS = {"PASS", "REVIEW", "BLOCK"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ChildEvent(BaseModel):
    """One line of the events file, as the dashboard accepts it."""

    type: Literal["stage.started", "stage.completed", "stage.retrying", "stage.skipped"]
    stage: str
    data: dict[str, int | str] = {}

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, value: str) -> str:
        if value not in STAGE_NAMES:
            raise ValueError("unknown stage")
        return value

    @field_validator("data", mode="before")
    @classmethod
    def _safe_data(cls, value: Any) -> dict[str, int | str]:
        # Before validation, so a bool or float is dropped instead of coerced to an int.
        if not isinstance(value, dict):
            raise ValueError("data must be an object")  # noqa: TRY004 - pydantic needs ValueError
        safe: dict[str, int | str] = {}
        for key, item in value.items():
            if key not in _ALLOWED_DATA:
                continue
            if key == "decision":
                if item in _DECISIONS:
                    safe[key] = item
            elif isinstance(item, int) and not isinstance(item, bool) and 0 <= item < 10**9:
                safe[key] = item
        return safe


class JsonlEventSink:
    """A pipeline StageObserver that appends one JSON line per stage event (child side)."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, kind: str, stage: str, data: dict[str, Any]) -> None:
        line = json.dumps({"type": f"stage.{kind}", "stage": stage, "data": data})
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


class EventJournal:
    """The ordered, bounded event list of one job, with ids that only increase."""

    def __init__(self, job_id: str, run_id: str) -> None:
        self.job_id, self.run_id = job_id, run_id
        self._events: list[VerificationEvent] = []
        self._next = 1
        self._cond = threading.Condition()
        self.dropped = 0

    @property
    def last_id(self) -> int:
        with self._cond:
            return self._next - 1

    @property
    def closed(self) -> bool:
        with self._cond:
            return bool(self._events) and self._events[-1].type in TERMINAL

    def append(self, type: EventType, *, stage: str | None = None, status: Any = None,
               message: str = "", data: dict[str, Any] | None = None) -> VerificationEvent | None:
        with self._cond:
            if self._events and self._events[-1].type in TERMINAL:
                return None  # nothing follows the end of a job
            if len(self._events) >= MAX_EVENTS - 1 and type not in TERMINAL:
                self.dropped += 1  # keep the last slot for the terminal event
                return None
            event = VerificationEvent(
                event_id=self._next, job_id=self.job_id, run_id=self.run_id, timestamp=_now(),
                type=type, stage=stage, status=status, message=message, data=data or {},
            )
            self._events.append(event)
            self._next += 1
            self._cond.notify_all()
            return event

    def since(self, last_id: int = 0) -> list[VerificationEvent]:
        with self._cond:
            return [e for e in self._events if e.event_id > last_id]

    def wait(self, last_id: int, timeout: float) -> bool:
        """Block until an event newer than last_id exists, or timeout; True if one does."""
        with self._cond:
            return self._cond.wait_for(lambda: self._next - 1 > last_id, timeout)

    def add_child_event(self, event: ChildEvent) -> None:
        status = event.type.removeprefix("stage.")
        labels = {"started": "started", "completed": "finished", "retrying": "retrying after a "
                  "transient model error", "skipped": "skipped (its inputs are missing)"}
        self.append(event.type, stage=event.stage, status=status,  # type: ignore[arg-type]
                    message=f"{event.stage.replace('_', ' ')} {labels[status]}", data=event.data)
        # The verdict is final only once deterministic_decide has run; announce it then.
        if (event.type == "stage.completed" and event.stage == "deterministic_decide"
                and "decision" in event.data):
            self.append("job.verdict_ready", status="running",
                        message=f"Deterministic verdict: {event.data['decision']}",
                        data={"decision": event.data["decision"]})
        if event.type == "stage.completed" and event.stage == "write_evidence":
            self.append("job.evidence_written", status="running",
                        message="proof.json, report.html and manifest.json written")


def parse_line(line: str) -> ChildEvent | None:
    if len(line) > MAX_LINE:
        return None
    try:
        return ChildEvent.model_validate_json(line)
    except (ValidationError, ValueError):
        return None


def tail_events(path: Path, journal: EventJournal, stop: threading.Event,
                interval: float = 0.2) -> None:
    """Copy valid lines from the events file into the journal until stop is set, then drain."""
    offset, partial = 0, b""
    while True:
        finished = stop.is_set()
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(256_000)
                offset += len(chunk)
        except OSError:
            chunk = b""
        lines = (partial + chunk).split(b"\n")
        partial = lines.pop()  # an incomplete last line waits for the rest
        if len(partial) > MAX_LINE:
            partial = b""
        for raw in lines:
            event = parse_line(raw.decode("utf-8", "replace"))
            if event is not None:
                journal.add_child_event(event)
        if finished and not chunk:
            return
        if not chunk:
            stop.wait(interval)


def sse_stream(journal: EventJournal, last_id: int, *, heartbeat: float = 15.0,
               max_seconds: float = 3600.0) -> Iterator[bytes]:
    """Server-sent event frames from last_id on; ends after the job's terminal event."""
    deadline = time.monotonic() + max_seconds
    yield b"retry: 2000\n\n"
    while time.monotonic() < deadline:
        for event in journal.since(last_id):
            last_id = event.event_id
            yield (f"id: {event.event_id}\nevent: {event.type}\n"
                   f"data: {event.model_dump_json()}\n\n").encode()
            if event.type in TERMINAL:
                return
        if not journal.wait(last_id, heartbeat):
            yield b": heartbeat\n\n"
