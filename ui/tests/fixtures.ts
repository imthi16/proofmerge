import type { RunDetail, Session } from "../src/api/types";

export const session: Session = {
  api_version: 1,
  token: "t",
  prs: true,
  stages: ["extract_intent", "deterministic_decide", "write_evidence"],
  model_stages: ["extract_intent"],
};

const HOSTILE = '<img src=x onerror="window.__xss=1">';

export function runDetail(decision: "PASS" | "REVIEW" | "BLOCK" = "REVIEW"): RunDetail {
  return {
    summary: {
      id: "set/run-1", run_id: "run-1", proof_path: "/tmp/it's/proof.json", decision, reasons: ["r"],
      base_sha: "a".repeat(40), head_sha: "b".repeat(40), hypotheses: 1, regressions: 0, tamper: 0,
      calls: 3, tokens: 30, endpoint: null, models: {}, orchestrator: "legacy",
      integrity: { status: "changed", problems: ["Hash mismatch: probes/H1.py."] },
      benchmark: null, label_disagrees: false, has_report: true, modified: 1, error: null,
    },
    proof: {
      proofmerge_version: "0.1.0", run_id: "run-1", base_sha: "a".repeat(40), head_sha: "b".repeat(40),
      diff_sha256: "0", language: "python",
      intent: { claims: [{ id: "C1", kind: "PRESERVE", statement: HOSTILE, source_quote: HOSTILE }], rejected: [] },
      hypotheses: {
        hypotheses: [{
          id: "H1", claim_id: "C1", title: `Title ${HOSTILE}`, changed_locations: ["a.py::f"],
          suspected_behavior: "breaks", expected_base: "ok", expected_head: "raises", falsification_criteria: "if not",
          possibly_intended: false, risk_category: "logic", test_intent: "t",
        }],
        rejected: [],
      },
      experiments: [{
        hypothesis_id: "H1", test_source_path: "p.py", test_source_sha256: "0", timeout_seconds: 60,
        executions: (["base", "head"] as const).flatMap((revision) => [1, 2, 3].map((attempt) => ({
          revision, commit_sha: (revision === "base" ? "a" : "b").repeat(40), attempt, command_argv: ["pytest"],
          exit_code: revision === "base" ? 0 : 1, outcome: revision === "base" ? ("PASS" as const) : ("FAIL" as const),
          duration_ms: 100, stdout_path: "o", stderr_path: "e", runner_image_digest: null,
          setup_failure: false,
        }))),
      }],
      findings: [{ hypothesis_id: "H1", claim_id: "C1", classification: "REGRESSION", verified: true, reproducer_command: "proofmerge replay x" }],
      tamper_findings: [],
      verdict: { decision, reasons: [`Recorded reason ${HOSTILE}`], unresolved_risks: [], scope_limitations: [] },
      model_usage: { calls: 3, prompt_tokens: 20, completion_tokens: 10, endpoint: null, models: {}, contributor_allowed: false },
      contested_claim_ids: [], mismatched_probe_ids: [], explained_probe_ids: [], explained_probe_claim_ids: {},
      orchestration: { engine: "legacy", langgraph_version: null, nodes: [], retries: [] },
      verdict_inputs: null,
    },
    probes: { H1: `def test_x():\n    assert "${HOSTILE}"` },
    failures: { H1: ["E   assert 1 == 2"] },
    logs: { "H1:head:1": { outcome: "FAIL", exit_code: 1, duration_ms: 100, command: ["pytest"], stdout: HOSTILE, stderr: "" } },
    replay_command: "proofmerge replay '/tmp/it'\"'\"'s/proof.json'",
  };
}
