import { describe, expect, it } from "vitest";
import type { VerificationEvent } from "../src/api/types";
import { mergeEvents, progressFrom } from "../src/lib/pipeline";

const STAGES = ["extract_intent", "generate_hypotheses", "deterministic_decide", "write_evidence"];
let id = 0;
function ev(type: VerificationEvent["type"], stage: string | null = null, data: VerificationEvent["data"] = {}): VerificationEvent {
  id += 1;
  return { version: 1, event_id: id, job_id: "j", run_id: "j", timestamp: "2026-11-01T00:00:00Z", type, stage, status: null, message: "", data };
}

describe("progressFrom", () => {
  it("has no decision until job.verdict_ready, whatever the stages say", () => {
    id = 0;
    const events = [
      ev("stage.started", "extract_intent"),
      ev("stage.completed", "extract_intent", { errors: 1 }),
      ev("stage.skipped", "generate_hypotheses"),
      ev("stage.completed", "deterministic_decide", { decision: "BLOCK" }),
    ];
    expect(progressFrom(STAGES, events).decision).toBeNull();
    const ready = progressFrom(STAGES, [...events, ev("job.verdict_ready", null, { decision: "BLOCK" })]);
    expect(ready.decision).toBe("BLOCK");
  });

  it("rejects a decision outside PASS/REVIEW/BLOCK", () => {
    id = 0;
    expect(progressFrom(STAGES, [ev("job.verdict_ready", null, { decision: "MERGE" })]).decision).toBeNull();
  });

  it("tracks stage states, retries and facts", () => {
    id = 0;
    const p = progressFrom(STAGES, [
      ev("job.fetch_started"),
      ev("job.fetch_completed"),
      ev("stage.started", "extract_intent"),
      ev("stage.retrying", "extract_intent", { attempt: 1, max_attempts: 3 }),
      ev("stage.completed", "extract_intent", { duration_ms: 40, claims: 2 }),
      ev("stage.skipped", "generate_hypotheses"),
      ev("stage.started", "deterministic_decide"),
    ]);
    expect(p.fetch).toBe("completed");
    expect(p.stages.map((s) => s.state)).toEqual(["completed", "skipped", "running", "pending"]);
    expect(p.stages[0]).toMatchObject({ durationMs: 40, facts: { claims: 2 } });
  });

  it("shows a retrying stage with its next attempt", () => {
    id = 0;
    const p = progressFrom(STAGES, [ev("stage.started", "extract_intent"), ev("stage.retrying", "extract_intent", { attempt: 1 })]);
    expect(p.stages[0]).toMatchObject({ state: "retrying", attempt: 2 });
  });

  it("is unchanged by replayed or out-of-order events", () => {
    id = 0;
    const a = ev("stage.started", "extract_intent");
    const b = ev("stage.completed", "extract_intent", { duration_ms: 5 });
    const once = progressFrom(STAGES, [a, b]);
    expect(progressFrom(STAGES, [b, a, b, a])).toEqual(once);
  });

  it("stops showing stages as running after the job fails", () => {
    id = 0;
    const p = progressFrom(STAGES, [ev("stage.started", "extract_intent"), ev("job.failed")]);
    expect(p.finished).toBe("failed");
    expect(p.stages[0]!.state).toBe("pending");
    expect(p.decision).toBeNull();
  });
});

describe("mergeEvents", () => {
  it("drops duplicates and keeps id order", () => {
    id = 0;
    const [a, b, c] = [ev("job.created"), ev("job.fetch_started"), ev("job.fetch_completed")];
    const merged = mergeEvents([a!, c!], [b!, c!, a!]);
    expect(merged.map((e) => e.event_id)).toEqual([1, 2, 3]);
    expect(mergeEvents(merged, [a!])).toBe(merged);
  });
});
