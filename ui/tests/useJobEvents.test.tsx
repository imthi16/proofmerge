import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobEvents, VerificationEvent } from "../src/api/types";
import { useJobEvents } from "../src/api/useJobEvents";

class FakeSource {
  static last: FakeSource | null = null;
  readyState = 0;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (e: MessageEvent) => void>();
  constructor(readonly url: string) {
    FakeSource.last = this;
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, fn);
  }
  close() {
    this.readyState = 2;
  }
  emit(event: VerificationEvent) {
    this.listeners.get(event.type)?.(new MessageEvent(event.type, { data: JSON.stringify(event) }));
  }
}

const open = (url: string) => new FakeSource(url) as unknown as EventSource;
const event = (event_id: number, type: VerificationEvent["type"], data = {}): VerificationEvent => ({
  version: 1, event_id, job_id: "j", run_id: "j", timestamp: "t", type, stage: null, status: null, message: type, data,
});

function snapshot(events: VerificationEvent[], status: "running" | "done" = "running"): JobEvents {
  return {
    job: { job_id: "j", run_id: "j", pr_number: 7, status, step: "", exit_code: null, output: "", head_sha: null,
      base_branch: null, created: 0, last_event_id: events.length },
    events, stages: ["write_evidence"], model_stages: [],
  };
}

function Probe() {
  const s = useJobEvents("j", open);
  return <div data-testid="out">{s.connection}|{s.events.map((e) => e.event_id).join(",")}</div>;
}

afterEach(() => vi.unstubAllGlobals());

describe("useJobEvents", () => {
  it("streams from the snapshot's last id and ignores replayed events", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(snapshot([event(1, "job.created"), event(2, "job.fetch_started")])))));
    render(<Probe />);
    await screen.findByText(/1,2/);
    expect(FakeSource.last!.url).toBe("/api/v1/jobs/j/events?last_event_id=2");
    act(() => {
      FakeSource.last!.onopen?.();
      FakeSource.last!.emit(event(2, "job.fetch_started"));
      FakeSource.last!.emit(event(3, "job.fetch_completed"));
    });
    expect(screen.getByTestId("out")).toHaveTextContent("live|1,2,3");
  });

  it("closes the stream at the job's end", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(snapshot([event(1, "job.created")])))));
    render(<Probe />);
    await screen.findByText(/\|1$/);
    const source = FakeSource.last!;
    act(() => source.emit(event(2, "job.completed")));
    expect(source.readyState).toBe(2);
    expect(screen.getByTestId("out")).toHaveTextContent("closed|1,2");
  });

  it("does not open a stream for a finished job", async () => {
    FakeSource.last = null;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(snapshot([event(1, "job.completed")], "done")))));
    render(<Probe />);
    await screen.findByText("closed|1");
    expect(FakeSource.last).toBeNull();
  });
});
