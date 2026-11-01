/* Generated from proofmerge.dashboard_models by scripts/gen-types.mjs. Do not edit. */

export interface ProofMergeApi {
  ApiError: ApiError;
  Benchmarks: Benchmarks;
  Diagnostics: Diagnostics;
  Job: Job;
  JobEvents: JobEvents;
  OverviewMetrics: OverviewMetrics;
  PullRequests: PullRequests;
  RunDetail: RunDetail;
  RunPage: RunPage;
  Session: Session;
  VerificationEvent: VerificationEvent;
  VerificationRequest: VerificationRequest;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "ApiError".
 */
export interface ApiError {
  error: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Benchmarks".
 */
export interface Benchmarks {
  benchmarks: BenchmarkSet[];
  known_runs: string[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "BenchmarkSet".
 */
export interface BenchmarkSet {
  candidates: BenchmarkCandidate[];
  date: string | null;
  git_sha: string | null;
  id: string;
  kind: "run" | "baselines";
  model: string | null;
  run_prefix: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "BenchmarkCandidate".
 */
export interface BenchmarkCandidate {
  a: string | null;
  b: string | null;
  b_reason: string | null;
  calls: number;
  decision: string | null;
  held_out: boolean;
  label: string;
  name: string;
  reasons: string[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Diagnostics".
 */
export interface Diagnostics {
  checks: Check[];
  default_orchestrator: string;
  evidence_root: string;
  python: string;
  repo_configured: boolean;
  runner_image: string;
  version: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Check".
 */
export interface Check {
  detail: string;
  name: string;
  ok: boolean | null;
}
/**
 * The one verification this dashboard is running or ran last.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Job".
 */
export interface Job {
  base_branch: string | null;
  created: number;
  exit_code: number | null;
  head_sha: string | null;
  job_id: string;
  last_event_id: number;
  output: string;
  pr_number: number;
  run_id: string;
  status: "queued" | "running" | "done" | "failed";
  step: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "JobEvents".
 */
export interface JobEvents {
  events: VerificationEvent[];
  job: Job;
  model_stages: string[];
  stages: string[];
}
/**
 * One step of a verification. Metadata only: no source, prompts, logs or secrets.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "VerificationEvent".
 */
export interface VerificationEvent {
  data: {
    [k: string]: number | string | boolean | null;
  };
  event_id: number;
  job_id: string;
  message: string;
  run_id: string;
  stage: string | null;
  status:
    | ("started" | "retrying" | "completed" | "skipped")
    | ("queued" | "running" | "done" | "failed")
    | null;
  timestamp: string;
  type:
    | "job.created"
    | "job.fetch_started"
    | "job.fetch_completed"
    | "stage.started"
    | "stage.retrying"
    | "stage.completed"
    | "stage.skipped"
    | "job.verdict_ready"
    | "job.evidence_written"
    | "job.completed"
    | "job.failed";
  version: 1;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "OverviewMetrics".
 */
export interface OverviewMetrics {
  benchmark_sets: number;
  counts: DecisionCounts;
  integrity: {
    [k: string]: number;
  };
  job: Job | null;
  model_calls: number;
  prs_enabled: boolean;
  recent: RunSummary[];
  root: string;
  tokens: number;
  total_runs: number;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "DecisionCounts".
 */
export interface DecisionCounts {
  BLOCK: number;
  PASS: number;
  REVIEW: number;
  unreadable: number;
}
/**
 * One row of the run list. `decision` is copied from proof.json, never derived.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "RunSummary".
 */
export interface RunSummary {
  base_sha: string | null;
  benchmark: BenchmarkLabel | null;
  calls: number;
  decision: ("PASS" | "REVIEW" | "BLOCK") | null;
  endpoint: string | null;
  error: string | null;
  has_report: boolean;
  head_sha: string | null;
  hypotheses: number;
  id: string;
  integrity: Integrity | null;
  label_disagrees: boolean;
  models: {
    [k: string]: string;
  };
  modified: number | null;
  orchestrator: string | null;
  proof_path: string | null;
  reasons: string[];
  regressions: number;
  run_id: string | null;
  tamper: number;
  tokens: number;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "BenchmarkLabel".
 */
export interface BenchmarkLabel {
  held_out: boolean;
  label: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Integrity".
 */
export interface Integrity {
  problems: string[];
  status: "intact" | "changed" | "unsealed";
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "PullRequests".
 */
export interface PullRequests {
  job: Job | null;
  prs: PullRequest[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "PullRequest".
 */
export interface PullRequest {
  author: string;
  base: string;
  body: string;
  draft: boolean;
  head: string;
  head_sha: string;
  number: number;
  older_runs: string[];
  runs: string[];
  state: string;
  title: string;
  updated: string;
  url: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "RunDetail".
 */
export interface RunDetail {
  failures: {
    [k: string]: string[];
  };
  logs: {
    [k: string]: ExecutionLog;
  };
  probes: {
    [k: string]: string | null;
  };
  proof: Proof;
  replay_command: string;
  summary: RunSummary;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "ExecutionLog".
 */
export interface ExecutionLog {
  command: string[];
  duration_ms: number;
  exit_code: number | null;
  outcome: "PASS" | "FAIL" | "ERROR" | "TIMEOUT";
  stderr: string;
  stdout: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Proof".
 */
export interface Proof {
  base_sha: string;
  contested_claim_ids: string[];
  diff_sha256: string;
  experiments: Experiment[];
  explained_probe_claim_ids: {
    [k: string]: string;
  };
  explained_probe_ids: string[];
  findings: Finding[];
  head_sha: string;
  hypotheses: HypothesisSet;
  intent: IntentContract;
  language: string;
  mismatched_probe_ids: string[];
  model_usage: ModelUsage;
  orchestration: OrchestrationRecord | null;
  proofmerge_version: string;
  run_id: string;
  tamper_findings: TamperFinding[];
  verdict: Verdict;
}
/**
 * A Spark-written probe for one hypothesis, and every run of it.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Experiment".
 */
export interface Experiment {
  executions: Execution[];
  hypothesis_id: string;
  test_source_path: string;
  test_source_sha256: string;
  timeout_seconds: number;
}
/**
 * One run of one probe on one revision.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Execution".
 */
export interface Execution {
  attempt: number;
  command_argv: string[];
  commit_sha: string;
  duration_ms: number;
  exit_code: number | null;
  outcome: "PASS" | "FAIL" | "ERROR" | "TIMEOUT";
  revision: "base" | "head";
  runner_image_digest: string | null;
  stderr_path: string;
  stdout_path: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Finding".
 */
export interface Finding {
  claim_id: string;
  classification:
    "REGRESSION" | "POSSIBLE_FIX" | "EXISTING_FAILURE" | "NO_DIFFERENCE" | "INCONCLUSIVE";
  hypothesis_id: string;
  reproducer_command: string;
  verified: boolean;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "HypothesisSet".
 */
export interface HypothesisSet {
  hypotheses: Hypothesis[];
  rejected: RejectedHypothesis[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Hypothesis".
 */
export interface Hypothesis {
  changed_locations: string[];
  claim_id: string;
  expected_base: string;
  expected_head: string;
  falsification_criteria: string;
  id: string;
  possibly_intended: boolean;
  risk_category: string;
  suspected_behavior: string;
  test_intent: string;
  title: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "RejectedHypothesis".
 */
export interface RejectedHypothesis {
  hypothesis: ProposedHypothesis;
  reason: string;
}
/**
 * A hypothesis as Spark returns it, before validation.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "ProposedHypothesis".
 */
export interface ProposedHypothesis {
  changed_locations: string[];
  claim_id: string;
  expected_base: string;
  expected_head: string;
  falsification_criteria: string;
  possibly_intended: boolean;
  risk_category: string;
  suspected_behavior: string;
  test_intent: string;
  title: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "IntentContract".
 */
export interface IntentContract {
  claims: IntentClaim[];
  rejected: ProposedClaim[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "IntentClaim".
 */
export interface IntentClaim {
  id: string;
  kind: "CHANGE" | "PRESERVE";
  source_quote: string | null;
  statement: string;
}
/**
 * A claim as Spark returns it, before the quote check.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "ProposedClaim".
 */
export interface ProposedClaim {
  kind: "CHANGE" | "PRESERVE";
  source_quote: string;
  statement: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "ModelUsage".
 */
export interface ModelUsage {
  calls: number;
  completion_tokens: number;
  contributor_allowed: boolean;
  endpoint: string | null;
  models: {
    [k: string]: string;
  };
  prompt_tokens: number;
}
/**
 * How the stages were scheduled. The verdict policy never reads this.
 *
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "OrchestrationRecord".
 */
export interface OrchestrationRecord {
  engine: "legacy" | "langgraph";
  langgraph_version: string | null;
  nodes: string[];
  retries: string[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "TamperFinding".
 */
export interface TamperFinding {
  base_test_on_head_outcome: ("PASS" | "FAIL" | "ERROR" | "TIMEOUT") | null;
  kind: "DELETED" | "SKIPPED" | "XFAIL" | "ASSERTION_REMOVED" | "MODIFIED";
  test_name: string | null;
  test_path: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Verdict".
 */
export interface Verdict {
  decision: "PASS" | "REVIEW" | "BLOCK";
  reasons: string[];
  scope_limitations: string[];
  unresolved_risks: string[];
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "RunPage".
 */
export interface RunPage {
  counts: DecisionCounts;
  limit: number;
  offset: number;
  root: string;
  run_sets: string[];
  runs: RunSummary[];
  total: number;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "Session".
 */
export interface Session {
  api_version: number;
  model_stages: string[];
  prs: boolean;
  stages: string[];
  token: string;
}
/**
 * This interface was referenced by `ProofMergeApi`'s JSON-Schema
 * via the `definition` "VerificationRequest".
 */
export interface VerificationRequest {
  number: number;
}
