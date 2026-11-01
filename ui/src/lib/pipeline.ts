import type { Decision, VerificationEvent } from "../api/types";

export type StageState = "pending" | "running" | "retrying" | "completed" | "skipped";

export interface StageView {
  name: string;
  state: StageState;
  durationMs: number | null;
  errors: number;
  attempt: number | null;
  facts: Record<string, number | string>;
}

export interface Progress {
  stages: StageView[];
  /** Set only by job.verdict_ready, which the server emits after deterministic_decide. */
  decision: Decision | null;
  evidenceWritten: boolean;
  finished: "done" | "failed" | null;
  fetch: "pending" | "running" | "completed";
  lastEventId: number;
}

const FACT_KEYS = ["claims", "hypotheses", "probes", "executions", "tamper", "findings", "verified"];

/**
 * Fold events into a per-stage view. Events are applied in id order and duplicates are
 * ignored, so a reconnect that replays events cannot double-count anything. The
 * decision is read from job.verdict_ready only: no stage, failure or count ever implies
 * one here.
 */
export function progressFrom(stages: string[], events: VerificationEvent[]): Progress {
  const byName = new Map<string, StageView>(
    stages.map((name) => [
      name,
      { name, state: "pending", durationMs: null, errors: 0, attempt: null, facts: {} },
    ]),
  );
  const progress: Progress = {
    stages: [],
    decision: null,
    evidenceWritten: false,
    finished: null,
    fetch: "pending",
    lastEventId: 0,
  };
  const seen = new Set<number>();
  for (const event of [...events].sort((a, b) => a.event_id - b.event_id)) {
    if (seen.has(event.event_id)) continue;
    seen.add(event.event_id);
    progress.lastEventId = Math.max(progress.lastEventId, event.event_id);
    const stage = event.stage ? byName.get(event.stage) : undefined;
    switch (event.type) {
      case "job.fetch_started":
        progress.fetch = "running";
        break;
      case "job.fetch_completed":
        progress.fetch = "completed";
        break;
      case "stage.started":
        if (stage && stage.state === "pending") stage.state = "running";
        break;
      case "stage.retrying":
        if (stage) {
          stage.state = "retrying";
          stage.attempt = typeof event.data.attempt === "number" ? event.data.attempt + 1 : null;
        }
        break;
      case "stage.skipped":
        if (stage) stage.state = "skipped";
        break;
      case "stage.completed":
        if (stage) {
          stage.state = "completed";
          stage.durationMs = typeof event.data.duration_ms === "number" ? event.data.duration_ms : null;
          stage.errors = typeof event.data.errors === "number" ? event.data.errors : 0;
          for (const key of FACT_KEYS) {
            const value = event.data[key];
            if (typeof value === "number") stage.facts[key] = value;
          }
        }
        break;
      case "job.verdict_ready": {
        const decision = event.data.decision;
        if (decision === "PASS" || decision === "REVIEW" || decision === "BLOCK") {
          progress.decision = decision;
        }
        break;
      }
      case "job.evidence_written":
        progress.evidenceWritten = true;
        break;
      case "job.completed":
        progress.finished = "done";
        break;
      case "job.failed":
        progress.finished = "failed";
        break;
      default:
        break;
    }
  }
  progress.stages = stages.map((name) => byName.get(name)!);
  if (progress.finished) {
    // A job that ended never runs its remaining stages; show them as not run.
    for (const stage of progress.stages) {
      if (stage.state === "running" || stage.state === "retrying") stage.state = "pending";
    }
  }
  return progress;
}

export function mergeEvents(
  known: VerificationEvent[],
  incoming: VerificationEvent[],
): VerificationEvent[] {
  const ids = new Set(known.map((e) => e.event_id));
  const fresh = incoming.filter((e) => !ids.has(e.event_id));
  return fresh.length ? [...known, ...fresh].sort((a, b) => a.event_id - b.event_id) : known;
}

export const STAGE_LABELS: Record<string, string> = {
  resolve_context: "Resolve context",
  load_trusted_config: "Load trusted config (base commit)",
  open_evidence_dir: "Open evidence directory",
  extract_intent: "Extract intent claims",
  generate_hypotheses: "Generate hypotheses",
  generate_execute_probes: "Write and run probes (base and head)",
  cross_check_claims: "Cross-check claims",
  check_probe_relevance: "Check probe relevance",
  run_tamper_guard: "Test-tamper guard",
  normalize_findings: "Normalise findings",
  deterministic_decide: "Deterministic verdict",
  write_evidence: "Seal evidence",
};

export const stageLabel = (name: string) => STAGE_LABELS[name] ?? name.replaceAll("_", " ");
