import { useMemo } from "react";
import { useJobEvents } from "../api/useJobEvents";
import type { Connection } from "../api/useJobEvents";
import { Link, routes } from "../app/router";
import { StageTimeline } from "../components/StageTimeline";
import { Empty, Loading } from "../components/Status";
import { VerdictBadge, VerdictHeadline } from "../components/Verdict";
import { clock } from "../lib/format";
import { progressFrom } from "../lib/pipeline";

const CONNECTION: Record<Connection, string> = {
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  closed: "Stream closed",
  unavailable: "Unavailable",
};

export function VerificationPage({ jobId }: { jobId: string }) {
  const stream = useJobEvents(jobId);
  const progress = useMemo(() => progressFrom(stream.stages, stream.events), [stream.stages, stream.events]);
  if (stream.notFound) {
    return (
      <Empty title="This verification is not in memory">
        Jobs are kept only while the dashboard runs. Finished runs stay on disk: see{" "}
        <Link to={routes.runs(new URLSearchParams({ sort: "recent" }))}>recent runs</Link>.
      </Empty>
    );
  }
  if (stream.error && !stream.job) return <Empty title="Could not load this verification">{stream.error}</Empty>;
  if (!stream.job) return <Loading what="the verification" />;
  const job = stream.job;
  const failed = progress.finished === "failed" || job.status === "failed";
  const done = progress.finished === "done" || job.status === "done";
  return (
    <div className="stack">
      <div className="page-head">
        <h1>PR #{job.pr_number} verification</h1>
        <p className="muted">
          Run <code>{job.run_id}</code>
          <span className={`conn conn-${stream.connection}`} role="status">
            {CONNECTION[stream.connection]}
          </span>
        </p>
      </div>
      <section className="panel verdict-slot" aria-live="polite" aria-labelledby="verdict-slot-h">
        <h2 id="verdict-slot-h" className="sr-only">
          Verdict
        </h2>
        {progress.decision ? (
          <div className="verdict-top">
            <VerdictBadge decision={progress.decision} large />
            <div>
              <VerdictHeadline decision={progress.decision} />
              <div className="muted">From the deterministic_decide stage (verdict.decide()).</div>
            </div>
          </div>
        ) : failed ? (
          <div>
            <strong>The verification did not finish.</strong>
            <div className="muted">
              {job.step}
              {job.exit_code != null ? ` (exit ${job.exit_code})` : ""}. No verdict was produced.
            </div>
          </div>
        ) : (
          <div>
            <strong>Verdict pending</strong>
            <div className="muted">
              A verdict exists only after the deterministic verdict stage has run on executed evidence. Stage failures
              along the way are not a verdict.
            </div>
          </div>
        )}
        {done ? (
          <div className="actions">
            <Link className="btn btn-primary" to={routes.run(job.run_id)}>
              Open the evidence
            </Link>
          </div>
        ) : null}
        {job.output && (failed || done) ? (
          <details>
            <summary>Command output</summary>
            <pre>{job.output}</pre>
          </details>
        ) : null}
      </section>
      <div className="split split-even">
        <section className="panel" aria-labelledby="stages-h">
          <h2 id="stages-h">Pipeline</h2>
          <StageTimeline progress={progress} modelStages={stream.modelStages} />
        </section>
        <section className="panel" aria-labelledby="activity-h">
          <h2 id="activity-h">Activity</h2>
          <ol className="activity" aria-live="off">
            {[...stream.events].reverse().map((event) => (
              <li key={event.event_id}>
                <span className="muted mono">{clock(event.timestamp)}</span> {event.message || event.type}
              </li>
            ))}
          </ol>
        </section>
      </div>
    </div>
  );
}
