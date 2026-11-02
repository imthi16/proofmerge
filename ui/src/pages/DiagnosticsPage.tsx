import { paths } from "../api/client";
import type { Diagnostics } from "../api/types";
import { useApi } from "../api/useApi";
import { useSession } from "../app/session";
import { ErrorBox, Loading } from "../components/Status";

const MARK = { ok: "✓", bad: "✕", na: "–" };

export function DiagnosticsPage() {
  const { data, error, loading, reload } = useApi<Diagnostics>(paths.diagnostics);
  const session = useSession();
  if (error && !data) return <ErrorBox onRetry={reload}>Could not run diagnostics: {error}</ErrorBox>;
  if (!data) return <Loading what="diagnostics" />;
  return (
    <div className="stack">
      <div className="page-head">
        <h1>Diagnostics</h1>
        <p className="muted">
          Whether this machine can verify. Presence and status only: no credential or command output is shown.
          <button type="button" className="btn btn-small" onClick={reload} disabled={loading}>
            {loading ? "Checking…" : "Check again"}
          </button>
        </p>
      </div>
      <section className="panel" aria-labelledby="checks-h">
        <h2 id="checks-h">Readiness</h2>
        <ul className="checks">
          {data.checks.map((check) => {
            const state = check.ok === true ? "ok" : check.ok === false ? "bad" : "na";
            return (
              <li key={check.name} className={`check-${state}`}>
                <span className="check-mark" aria-hidden="true">
                  {MARK[state]}
                </span>
                <strong>{check.name}</strong>
                <span className="sr-only">{state === "ok" ? "ready" : state === "bad" ? "problem" : "not applicable"}:</span>
                <span className="muted">{check.detail}</span>
              </li>
            );
          })}
        </ul>
      </section>
      <section className="panel" aria-labelledby="about-h">
        <h2 id="about-h">This dashboard</h2>
        <dl className="facts">
          <dt>ProofMerge</dt>
          <dd>{data.version}</dd>
          <dt>Python</dt>
          <dd>{data.python}</dd>
          <dt>Default orchestrator</dt>
          <dd>{data.default_orchestrator}</dd>
          <dt>Runner image</dt>
          <dd>
            <code>{data.runner_image}</code>
          </dd>
          <dt>Evidence folder</dt>
          <dd>
            <code>{data.evidence_root}</code>
          </dd>
          <dt>Pull requests</dt>
          <dd>{data.repo_configured ? "enabled (--repo)" : "off: start with --repo to list and verify PRs"}</dd>
          <dt>Pipeline stages</dt>
          <dd>{session ? session.stages.length : "—"}</dd>
        </dl>
      </section>
    </div>
  );
}
