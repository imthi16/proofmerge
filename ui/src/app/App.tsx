import { useEffect, useState } from "react";
import { getJSON, paths } from "../api/client";
import type { Job, Session } from "../api/types";
import { Link, match, routes, Router, useRouter } from "./router";
import { SessionContext } from "./session";
import { useTheme } from "./theme";
import type { Theme } from "./theme";
import { OverviewPage } from "../pages/OverviewPage";
import { RunsPage } from "../pages/RunsPage";
import { RunPage } from "../pages/RunPage";
import { PullRequestsPage } from "../pages/PullRequestsPage";
import { VerificationPage } from "../pages/VerificationPage";
import { BenchmarksPage } from "../pages/BenchmarksPage";
import { DiagnosticsPage } from "../pages/DiagnosticsPage";

const NAV = [
  { to: routes.overview, label: "Overview", active: (p: string) => p === "/" },
  {
    to: routes.pullRequests(),
    label: "Pull requests",
    active: (p: string) => p.startsWith("/pull-requests") || p.startsWith("/verifications"),
  },
  { to: routes.runs(), label: "Runs", active: (p: string) => p.startsWith("/runs") },
  { to: routes.benchmarks, label: "Benchmarks", active: (p: string) => p.startsWith("/benchmarks") },
  { to: routes.diagnostics, label: "Diagnostics", active: (p: string) => p.startsWith("/diagnostics") },
];

const TITLES: [string, string][] = [
  ["/", "Overview"],
  ["/pull-requests", "Pull requests"],
  ["/verifications/:jobId", "Live verification"],
  ["/runs", "Runs"],
  ["/runs/:runId", "Evidence"],
  ["/benchmarks", "Benchmarks"],
  ["/diagnostics", "Diagnostics"],
];

function Page() {
  const { path } = useRouter();
  let params: Record<string, string> | null;
  if (path === "/") return <OverviewPage />;
  if (match("/pull-requests", path)) return <PullRequestsPage />;
  if ((params = match("/verifications/:jobId", path))) return <VerificationPage jobId={params.jobId!} />;
  if (match("/runs", path)) return <RunsPage />;
  if ((params = match("/runs/:runId", path))) return <RunPage runId={params.runId!} />;
  if (match("/benchmarks", path)) return <BenchmarksPage />;
  if (match("/diagnostics", path)) return <DiagnosticsPage />;
  return (
    <section className="panel empty">
      <strong>Page not found</strong>
      <Link to={routes.overview}>Go to the overview</Link>
    </section>
  );
}

function ActiveJob() {
  // A small indicator that a verification is running, visible from every page.
  const { path } = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    let live = true;
    const poll = () =>
      getJSON<Job | null>(paths.activeJob)
        .then((value) => live && setJob(value))
        .catch(() => undefined);
    void poll();
    const timer = window.setInterval(() => !document.hidden && void poll(), 5000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [path]);
  if (!job || job.status !== "running") return null;
  return (
    <Link className="pill-running" to={routes.verification(job.job_id)}>
      <span className="spinner" aria-hidden="true" /> Verifying PR #{job.pr_number}
    </Link>
  );
}

function ThemePicker({ theme, setTheme }: { theme: Theme; setTheme: (t: Theme) => void }) {
  return (
    <label className="theme">
      <span className="sr-only">Theme</span>
      <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)} aria-label="Colour theme">
        <option value="system">System theme</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

function Shell({ session }: { session: Session | null }) {
  const { path } = useRouter();
  const [theme, setTheme] = useTheme();
  useEffect(() => {
    const title = TITLES.find(([pattern]) => match(pattern, path))?.[1];
    document.title = title ? `${title} · ProofMerge` : "ProofMerge Control Center";
  }, [path]);
  return (
    <SessionContext.Provider value={session}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="top">
        <Link to={routes.overview} className="brand" aria-label="ProofMerge Control Center, overview">
          <img src="/static/app/icon.svg" alt="" width={24} height={24} />
          ProofMerge <span>Control Center</span>
        </Link>
        <nav aria-label="Main">
          <ul className="nav">
            {NAV.map((item) => (
              <li key={item.label}>
                <Link to={item.to} aria-current={item.active(path) ? "page" : undefined}>
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <span className="spacer" />
        <ActiveJob />
        <ThemePicker theme={theme} setTheme={setTheme} />
      </header>
      <main id="main" tabIndex={-1}>
        <Page />
      </main>
      <footer className="foot">
        Verdicts come from ProofMerge's deterministic policy and executed evidence. PASS means no regression
        was found within the tested scope, not that the change is correct.{" "}
        <a href="/legacy/">Previous dashboard</a>
      </footer>
    </SessionContext.Provider>
  );
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => {
    getJSON<Session>(paths.session)
      .then(setSession)
      .catch(() => setSession(null));
  }, []);
  return (
    <Router>
      <Shell session={session} />
    </Router>
  );
}
