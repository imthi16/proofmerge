import type { ExecutionLog, Experiment } from "../api/types";
import { duration, short } from "../lib/format";

interface GridProps {
  experiment: Experiment | undefined;
  open: string;
  onOpen: (key: string) => void;
}

/** Base and head outcomes per attempt, as recorded. Each cell opens that run's log. */
export function ExecutionGrid({ experiment, open, onOpen }: GridProps) {
  if (!experiment || !experiment.executions.length) {
    return <p className="muted">No probe was run for this hypothesis.</p>;
  }
  const attempts = [...new Set(experiment.executions.map((e) => e.attempt))].sort((a, b) => a - b);
  return (
    <table className="grid">
      <caption className="sr-only">Probe outcome per revision and attempt</caption>
      <thead>
        <tr>
          <th scope="col">Revision</th>
          {attempts.map((a) => (
            <th key={a} scope="col">
              attempt {a}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {(["base", "head"] as const).map((revision) => {
          const runs = experiment.executions.filter((e) => e.revision === revision);
          return (
            <tr key={revision}>
              <th scope="row">
                {revision} <code>{short(runs[0]?.commit_sha)}</code>
              </th>
              {attempts.map((a) => {
                const run = runs.find((e) => e.attempt === a);
                const outcome = run?.outcome ?? "—";
                const key = `${revision}:${a}`;
                return (
                  <td key={a}>
                    <button
                      type="button"
                      className={`cell cell-${outcome.toLowerCase()}`}
                      aria-pressed={open === key}
                      disabled={!run}
                      onClick={() => onOpen(open === key ? "" : key)}
                      aria-label={`${revision} attempt ${a}: ${outcome}${run ? `, ${duration(run.duration_ms)}` : ""}. Show its log`}
                    >
                      {outcome}
                      {run ? <span className="cell-ms">{duration(run.duration_ms)}</span> : null}
                    </button>
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function LogView({ log, label }: { log: ExecutionLog | undefined; label: string }) {
  if (!log) return <p className="muted">No log was recorded for {label}.</p>;
  return (
    <div className="log" aria-label={`Log for ${label}`}>
      <div className="log-head">
        <strong>{label}</strong>
        <span className="muted">
          {log.outcome}, exit {log.exit_code ?? "none (timed out)"}, {duration(log.duration_ms)}
        </span>
      </div>
      <pre className="cmd">{log.command.join(" ")}</pre>
      <pre>{log.stdout || "(no stdout)"}</pre>
      {log.stderr ? (
        <details>
          <summary>stderr</summary>
          <pre>{log.stderr}</pre>
        </details>
      ) : null}
    </div>
  );
}
