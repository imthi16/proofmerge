import type { Progress, StageView } from "../lib/pipeline";
import { stageLabel } from "../lib/pipeline";
import { duration, plural } from "../lib/format";

const STATE_TEXT: Record<StageView["state"], string> = {
  pending: "waiting",
  running: "running",
  retrying: "retrying",
  completed: "done",
  skipped: "skipped",
};
const MARK: Record<StageView["state"], string> = {
  pending: "○",
  running: "●",
  retrying: "↻",
  completed: "✓",
  skipped: "–",
};

function facts(stage: StageView): string {
  const f = stage.facts;
  const parts: string[] = [];
  if (typeof f.claims === "number") parts.push(plural(f.claims, "claim"));
  if (typeof f.hypotheses === "number") parts.push(plural(f.hypotheses, "hypothesis", "hypotheses"));
  if (typeof f.probes === "number") parts.push(plural(f.probes, "probe"));
  if (typeof f.executions === "number") parts.push(plural(f.executions, "sandbox run"));
  if (typeof f.tamper === "number") parts.push(plural(f.tamper, "tamper finding"));
  if (typeof f.findings === "number") parts.push(plural(f.findings, "finding"));
  if (typeof f.verified === "number" && f.verified) parts.push(`${f.verified} verified`);
  if (stage.errors) parts.push(plural(stage.errors, "stage error"));
  return parts.join(" · ");
}

export function StageTimeline({ progress, modelStages }: { progress: Progress; modelStages: string[] }) {
  const fetchState = progress.fetch === "completed" ? "completed" : progress.fetch === "running" ? "running" : "pending";
  return (
    <ol className="timeline" aria-label="Verification stages">
      <li className={`stage stage-${fetchState}`}>
        <span className="stage-mark" aria-hidden="true">
          {MARK[fetchState]}
        </span>
        <span className="stage-name">Fetch the PR into refs/proofmerge/</span>
        <span className="stage-state">{STATE_TEXT[fetchState]}</span>
      </li>
      {progress.stages.map((stage) => (
        <li key={stage.name} className={`stage stage-${stage.state}`} aria-current={stage.state === "running" ? "step" : undefined}>
          <span className="stage-mark" aria-hidden="true">
            {MARK[stage.state]}
          </span>
          <span className="stage-name">
            {stageLabel(stage.name)}
            {modelStages.includes(stage.name) ? <span className="tag">model</span> : null}
            {stage.name === "deterministic_decide" ? <span className="tag">verdict.decide()</span> : null}
          </span>
          <span className="stage-state">
            {STATE_TEXT[stage.state]}
            {stage.state === "retrying" && stage.attempt ? ` (attempt ${stage.attempt})` : ""}
            {stage.state === "completed" ? ` · ${duration(stage.durationMs)}` : ""}
          </span>
          {stage.state === "completed" && facts(stage) ? <span className="stage-facts">{facts(stage)}</span> : null}
        </li>
      ))}
    </ol>
  );
}
