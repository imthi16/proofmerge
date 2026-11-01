import { paths } from "../api/client";
import type { OverviewMetrics } from "../api/types";
import { useApi } from "../api/useApi";
import { Link, routes, useRouter } from "../app/router";
import { DecisionBar } from "../components/DecisionBar";
import { Seal } from "../components/Seal";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { VerdictBadge } from "../components/Verdict";
import { short, when } from "../lib/format";

export function OverviewPage() {
  const { data, error, loading, reload } = useApi<OverviewMetrics>(paths.overview);
  const { navigate } = useRouter();
  if (error && !data) {
    return (
      <ErrorBox onRetry={reload}>
        Could not read the evidence folder: {error}. Is <code>proofmerge dashboard</code> still running?
      </ErrorBox>
    );
  }
  if (!data) return <Loading what="the overview" />;
  const job = data.job;
  return (
    <div className="stack">
      <div className="page-head">
        <h1>Overview</h1>
        <p className="muted">
          Evidence folder <code>{data.root}</code>
          <button type="button" className="btn btn-small" onClick={reload} disabled={loading}>
            {loading ? "Scanning…" : "Refresh"}
          </button>
        </p>
      </div>

      {job ? (
        <section className={`panel job-card job-${job.status}`} aria-label="Latest verification">
          <div>
            <strong>
              PR #{job.pr_number}:{" "}
              {job.status === "running" ? job.step : job.status === "done" ? "verified" : "verification failed"}
            </strong>
            <div className="muted">
              Run <code>{job.run_id}</code>
            </div>
          </div>
          <div className="actions">
            <Link className="btn btn-primary" to={routes.verification(job.job_id)}>
              {job.status === "running" ? "Watch live" : "See the timeline"}
            </Link>
            {job.status === "done" ? (
              <Link className="btn" to={routes.run(job.run_id)}>
                Open the evidence
              </Link>
            ) : null}
          </div>
        </section>
      ) : null}

      {data.total_runs === 0 ? (
        <Empty title="No runs in this folder yet">
          Run <code>proofmerge verify … --evidence-dir {data.root}/my-run</code>
          {data.prs_enabled ? (
            <>
              , or verify a pull request from <Link to={routes.pullRequests()}>Pull requests</Link>
            </>
          ) : null}
          .
        </Empty>
      ) : (
        <>
          <section aria-labelledby="verdicts-h">
            <h2 id="verdicts-h">Recorded verdicts</h2>
            <DecisionBar
              counts={data.counts}
              onPick={(d) => navigate(routes.runs(new URLSearchParams(d ? { decision: d } : {})))}
            />
            {data.counts.unreadable ? (
              <p className="muted">{data.counts.unreadable} run(s) have an unreadable proof.json.</p>
            ) : null}
          </section>

          <section className="tiles" aria-label="Totals">
            <div className="tile">
              <span className="tile-n">{data.total_runs}</span>
              <span className="tile-k">runs</span>
            </div>
            <div className="tile">
              <span className="tile-n">{data.integrity.intact ?? 0}</span>
              <span className="tile-k">evidence intact</span>
            </div>
            <div className={`tile${data.integrity.changed ? " tile-warn" : ""}`}>
              <span className="tile-n">{data.integrity.changed ?? 0}</span>
              <span className="tile-k">evidence changed</span>
            </div>
            <div className="tile">
              <span className="tile-n">{data.integrity.unsealed ?? 0}</span>
              <span className="tile-k">without manifest</span>
            </div>
            <div className="tile">
              <span className="tile-n">{data.model_calls.toLocaleString()}</span>
              <span className="tile-k">model calls</span>
            </div>
            <div className="tile">
              <span className="tile-n">{data.tokens.toLocaleString()}</span>
              <span className="tile-k">tokens</span>
            </div>
          </section>

          <section className="panel" aria-labelledby="recent-h">
            <div className="panel-head">
              <h2 id="recent-h">Recent runs</h2>
              <Link to={routes.runs(new URLSearchParams({ sort: "recent" }))}>All runs</Link>
            </div>
            <ul className="recent">
              {data.recent.map((run) => (
                <li key={run.id}>
                  <VerdictBadge decision={run.decision ?? null} />
                  <Link to={routes.run(run.id)} className="recent-id">
                    {run.id}
                  </Link>
                  <span className="muted mono">
                    {short(run.base_sha)} → {short(run.head_sha)}
                  </span>
                  <Seal integrity={run.integrity} />
                  <span className="muted">{when(run.modified)}</span>
                </li>
              ))}
            </ul>
          </section>
          {data.benchmark_sets ? (
            <p className="muted">
              {data.benchmark_sets} benchmark result set(s) here. <Link to={routes.benchmarks}>See benchmarks</Link>
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
