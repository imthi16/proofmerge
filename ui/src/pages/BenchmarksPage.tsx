import { useState } from "react";
import { paths } from "../api/client";
import type { BenchmarkCandidate, Benchmarks } from "../api/types";
import { useApi } from "../api/useApi";
import { Link, routes } from "../app/router";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { short } from "../lib/format";

type Slice = "all" | "held" | "seen";
const COLS = ["BLOCK", "REVIEW", "PASS", "ERROR"] as const;

// Benchmark labels say whether a synthetic change is faulty (BLOCK) or safe (NOT_BLOCK).
// These tables compare recorded decisions with labels; they never change a decision.
function Matrix({ rows }: { rows: BenchmarkCandidate[] }) {
  return (
    <table className="matrix">
      <caption className="sr-only">Recorded decision per benchmark label</caption>
      <thead>
        <tr>
          <th scope="col">Label</th>
          {COLS.map((c) => (
            <th key={c} scope="col">
              {c}
            </th>
          ))}
          <th scope="col">Total</th>
        </tr>
      </thead>
      <tbody>
        {([["BLOCK", "Faulty"], ["NOT_BLOCK", "Safe"]] as const).map(([label, name]) => {
          const group = rows.filter((r) => r.label === label);
          return (
            <tr key={label}>
              <th scope="row">{name}</th>
              {COLS.map((c) => {
                const n = group.filter((r) => r.decision === c).length;
                const miss = n > 0 && ((label === "BLOCK" && c === "PASS") || (label === "NOT_BLOCK" && c === "BLOCK"));
                const hit = n > 0 && ((label === "BLOCK" && c === "BLOCK") || (label === "NOT_BLOCK" && c === "PASS"));
                return (
                  <td key={c} className={miss ? "miss" : hit ? "hit" : ""}>
                    {n}
                    {miss ? <span className="sr-only"> (wrong)</span> : null}
                  </td>
                );
              })}
              <td>{group.length}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const disagrees = (r: BenchmarkCandidate) =>
  r.label === "BLOCK" ? r.decision !== "BLOCK" : r.decision === "BLOCK";

export function BenchmarksPage() {
  const { data, error, reload } = useApi<Benchmarks>(paths.benchmarks);
  const [slice, setSlice] = useState<Slice>("all");
  if (error && !data) return <ErrorBox onRetry={reload}>Could not read benchmarks: {error}</ErrorBox>;
  if (!data) return <Loading what="benchmarks" />;
  const known = new Set(data.known_runs);
  const pick = (rows: BenchmarkCandidate[]) =>
    rows.filter((r) => slice === "all" || (slice === "held" ? r.held_out : !r.held_out));
  const runs = data.benchmarks.filter((b) => b.kind === "run");
  const baselines = data.benchmarks.filter((b) => b.kind === "baselines");
  const blocked = (rows: BenchmarkCandidate[], key: "a" | "b" | "decision") => ({
    faulty: rows.filter((r) => r.label === "BLOCK" && r[key] === "BLOCK").length,
    safe: rows.filter((r) => r.label === "NOT_BLOCK" && r[key] === "BLOCK").length,
  });
  const faultyN = (rows: BenchmarkCandidate[]) => rows.filter((r) => r.label === "BLOCK").length;
  const safeN = (rows: BenchmarkCandidate[]) => rows.filter((r) => r.label === "NOT_BLOCK").length;
  return (
    <div className="stack">
      <div className="page-head">
        <h1>Benchmarks</h1>
        <p className="muted">A smoke benchmark on synthetic changes, not an accuracy claim.</p>
      </div>
      {!data.benchmarks.length ? (
        <Empty title="No benchmark results here">
          Point the dashboard at a folder holding a <code>results.json</code> from <code>bench/run_bench.py</code>, such
          as <code>bench/results</code>.
        </Empty>
      ) : (
        <>
          <div className="seg-ctl" role="group" aria-label="Candidates">
            {([["all", "All"], ["held", "Held out"], ["seen", "Not held out"]] as const).map(([key, label]) => (
              <button key={key} type="button" aria-pressed={slice === key} onClick={() => setSlice(key)}>
                {label}
              </button>
            ))}
          </div>
          {[...runs].reverse().map((b) => {
            const rows = pick(b.candidates);
            const misses = rows.filter(disagrees);
            const names = b.candidates.map((r) => r.name).sort().join();
            // Baselines count only when they ran on exactly the same candidates.
            const base = baselines.find((x) => x.candidates.map((r) => r.name).sort().join() === names);
            const baseRows = base ? pick(base.candidates) : [];
            const a = blocked(baseRows, "a");
            const bb = blocked(baseRows, "b");
            const pm = blocked(rows, "decision");
            return (
              <section key={b.id} className="panel" aria-labelledby={`b-${b.id}`}>
                <h2 id={`b-${b.id}`}>{b.id}</h2>
                <p className="muted">
                  {b.git_sha ? (
                    <>
                      commit <code>{short(b.git_sha)}</code>,{" "}
                    </>
                  ) : null}
                  {b.model || "model not recorded"}
                  {b.date ? `, ${new Date(b.date).toLocaleString()}` : ""}
                </p>
                <Matrix rows={rows} />
                {base ? (
                  <>
                    <h3>Against simple baselines</h3>
                    <table className="matrix">
                      <thead>
                        <tr>
                          <th scope="col">Method</th>
                          <th scope="col">Faulty blocked</th>
                          <th scope="col">Safe blocked</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <th scope="row">Base tests on head (A)</th>
                          <td>{a.faulty} / {faultyN(baseRows)}</td>
                          <td>{a.safe} / {safeN(baseRows)}</td>
                        </tr>
                        <tr>
                          <th scope="row">One-shot Spark opinion (B)</th>
                          <td>{bb.faulty} / {faultyN(baseRows)}</td>
                          <td>{bb.safe} / {safeN(baseRows)}</td>
                        </tr>
                        <tr>
                          <th scope="row">ProofMerge (BLOCK needs base PASS 3/3, head FAIL 3/3)</th>
                          <td>{pm.faulty} / {faultyN(rows)}</td>
                          <td>{pm.safe} / {safeN(rows)}</td>
                        </tr>
                      </tbody>
                    </table>
                    <p className="muted">
                      Baselines from <code>{base.id}</code>. B gives an opinion with no reproducer; a ProofMerge BLOCK
                      comes with a replayable probe.
                    </p>
                  </>
                ) : null}
                {misses.length ? (
                  <>
                    <h3>Not matching the label</h3>
                    <ul className="reasons">
                      {misses.map((r) => (
                        <li key={r.name}>
                          {known.has(`${b.run_prefix}${r.name}`) ? (
                            <Link to={routes.run(`${b.run_prefix}${r.name}`)}>{r.name}</Link>
                          ) : (
                            <strong>{r.name}</strong>
                          )}
                          <span className="muted">
                            {" "}
                            — label {r.label}, got {r.decision}
                            {r.held_out ? ", held out" : ""}.
                          </span>{" "}
                          {r.reasons[0] ?? ""}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </section>
            );
          })}
        </>
      )}
    </div>
  );
}
