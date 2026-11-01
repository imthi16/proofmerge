import { useEffect, useMemo, useRef, useState } from "react";
import { getJSON, paths } from "../api/client";
import type { RunPage as RunPageData, RunSummary } from "../api/types";
import { useApi } from "../api/useApi";
import { Link, routes, useRouter } from "../app/router";
import { DecisionBar } from "../components/DecisionBar";
import { Seal } from "../components/Seal";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { VerdictBadge } from "../components/Verdict";
import { short, when } from "../lib/format";

const PAGE = 50;
const FILTERS = ["decision", "q", "integrity", "set", "disagrees", "sort", "offset"] as const;

function csv(rows: RunSummary[]): string {
  const cols = ["id", "decision", "regressions", "hypotheses", "calls", "tokens", "integrity",
    "label", "held_out", "label_disagrees", "base_sha", "head_sha"];
  const cell = (v: unknown) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const lines = rows.map((r) =>
    [r.id, r.decision ?? "ERROR", r.regressions, r.hypotheses, r.calls, r.tokens, r.integrity?.status,
      r.benchmark?.label, r.benchmark?.held_out, r.label_disagrees, r.base_sha, r.head_sha]
      .map(cell)
      .join(","),
  );
  return [cols.join(","), ...lines].join("\n") + "\n";
}

export function RunsPage() {
  const { query, navigate } = useRouter();
  const params = useMemo(() => {
    const p = new URLSearchParams();
    for (const key of FILTERS) {
      const value = query.get(key);
      if (value) p.set(key, value);
    }
    p.set("limit", String(PAGE));
    return p;
  }, [query]);
  const { data, error, loading, reload } = useApi<RunPageData>(paths.runs(params));
  const [text, setText] = useState(query.get("q") ?? "");
  const search = useRef<HTMLInputElement>(null);

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(query);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("offset" in changes)) next.delete("offset");
    navigate(routes.runs(next), { replace: true });
  };

  // Debounce typing into the URL (and so into the request).
  useEffect(() => {
    if (text === (query.get("q") ?? "")) return;
    const timer = window.setTimeout(() => update({ q: text.trim() }), 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === "/" && target?.tagName !== "INPUT" && target?.tagName !== "SELECT") {
        event.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const exportCsv = async () => {
    const all = new URLSearchParams(params);
    all.set("limit", "500");
    all.delete("offset");
    const page = await getJSON<RunPageData>(paths.runs(all));
    const url = URL.createObjectURL(new Blob([csv(page.runs)], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `proofmerge-runs-${page.root}.csv` });
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  if (error && !data) return <ErrorBox onRetry={reload}>Could not read runs: {error}</ErrorBox>;
  if (!data) return <Loading what="runs" />;
  const offset = data.offset;
  const anyFilter = FILTERS.some((key) => key !== "sort" && key !== "offset" && query.get(key));
  const hasLabels = data.runs.some((r) => r.benchmark) || query.get("disagrees") === "1";

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Runs</h1>
        <p className="muted">
          Every <code>proof.json</code> under <code>{data.root}</code>
        </p>
      </div>
      <DecisionBar counts={data.counts} active={query.get("decision") ?? ""} onPick={(d) => update({ decision: d })} />
      <div className="filters" role="search">
        <input
          ref={search}
          className="input grow"
          type="search"
          placeholder="Search runs and reasons ( / )"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Search runs"
        />
        {data.run_sets.length > 1 ? (
          <select className="input" value={query.get("set") ?? ""} onChange={(e) => update({ set: e.target.value })} aria-label="Run set">
            <option value="">All run sets</option>
            {data.run_sets.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        ) : null}
        <select className="input" value={query.get("integrity") ?? ""} onChange={(e) => update({ integrity: e.target.value })} aria-label="Evidence integrity">
          <option value="">Any evidence state</option>
          <option value="intact">Evidence intact</option>
          <option value="changed">Evidence changed</option>
          <option value="unsealed">No manifest</option>
        </select>
        <select className="input" value={query.get("decision") === "ERROR" ? "ERROR" : ""} onChange={(e) => update({ decision: e.target.value })} aria-label="Unreadable runs">
          <option value="">Readable or not</option>
          <option value="ERROR">Unreadable only</option>
        </select>
        <select className="input" value={query.get("sort") ?? "verdict"} onChange={(e) => update({ sort: e.target.value })} aria-label="Sort">
          <option value="verdict">Sort: verdict</option>
          <option value="recent">Sort: newest</option>
          <option value="name">Sort: name</option>
          <option value="regressions">Sort: regressions</option>
          <option value="calls">Sort: model calls</option>
        </select>
        {hasLabels ? (
          <label className="check">
            <input type="checkbox" checked={query.get("disagrees") === "1"} onChange={(e) => update({ disagrees: e.target.checked ? "1" : "" })} />
            Disagrees with benchmark label
          </label>
        ) : null}
        <button type="button" className="btn" onClick={() => void exportCsv()} disabled={!data.total}>
          Export CSV
        </button>
      </div>

      {data.total === 0 ? (
        anyFilter ? (
          <Empty title="No run matches">
            Change the search or filters.{" "}
            <button type="button" className="btn btn-small" onClick={() => { setText(""); navigate(routes.runs(), { replace: true }); }}>
              Clear filters
            </button>
          </Empty>
        ) : (
          <Empty title="No runs in this folder yet">
            Run <code>proofmerge verify … --evidence-dir {data.root}/my-run</code>, then refresh.
          </Empty>
        )
      ) : (
        <div className="panel table-wrap" aria-busy={loading}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Verdict</th>
                <th scope="col">Run</th>
                <th scope="col" className="hide-sm">Base → head</th>
                <th scope="col" className="num">Regressions</th>
                <th scope="col" className="num hide-sm">Calls</th>
                <th scope="col" className="hide-md">Written</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((run) => (
                <tr key={run.id}>
                  <td>
                    <VerdictBadge decision={run.decision ?? null} />
                  </td>
                  <td>
                    <Link to={routes.run(run.id)} className="row-link">
                      {run.id}
                    </Link>
                    <div className="sub">
                      <Seal integrity={run.integrity} />
                      {run.benchmark ? (
                        <span>
                          label {run.benchmark.label}
                          {run.benchmark.held_out ? ", held out" : ""}
                          {run.label_disagrees ? <strong className="warn"> · disagrees</strong> : null}
                        </span>
                      ) : null}
                      {run.error ? <span className="warn">{run.error}</span> : null}
                    </div>
                  </td>
                  <td className="mono hide-sm">
                    {short(run.base_sha)} → {short(run.head_sha)}
                  </td>
                  <td className="num">{run.decision ? run.regressions : "—"}</td>
                  <td className="num hide-sm">{run.decision ? run.calls : "—"}</td>
                  <td className="hide-md muted">{when(run.modified)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pager">
            <span className="muted">
              {offset + 1}–{offset + data.runs.length} of {data.total}
            </span>
            <button type="button" className="btn" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - PAGE)) })}>
              Previous
            </button>
            <button type="button" className="btn" disabled={offset + PAGE >= data.total} onClick={() => update({ offset: String(offset + PAGE) })}>
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
