// The API's types come from the Python models (generated.ts). Only display helpers
// live here; nothing in the client computes or changes a verdict.
export type {
  Benchmarks,
  BenchmarkCandidate,
  BenchmarkSet,
  Check,
  DecisionCounts,
  Diagnostics,
  Execution,
  ExecutionLog,
  Experiment,
  Finding,
  Hypothesis,
  IntentClaim,
  Job,
  JobEvents,
  OverviewMetrics,
  Proof,
  PullRequest,
  PullRequests,
  RunDetail,
  RunPage,
  RunSummary,
  Session,
  TamperFinding,
  Verdict,
  VerificationEvent,
} from "./generated";

import type { Verdict } from "./generated";

export type Decision = Verdict["decision"];
export const DECISIONS: readonly Decision[] = ["BLOCK", "REVIEW", "PASS"];

// Wording for a decision that verdict.decide() already made. PASS is never "correct".
export const HEADLINE: Record<Decision, string> = {
  BLOCK: "Verified regression — do not merge",
  REVIEW: "Not conclusive — a human must review",
  PASS: "No regression found within the tested scope",
};
