import { useMemo, useState } from "react";
import { paths, startVerification } from "../api/client";
import type { PullRequest, PullRequests, RunPage } from "../api/types";
import { useApi } from "../api/useApi";
import { Link, routes, useRouter } from "../app/router";
import { useSession } from "../app/session";
import { CopyButton } from "../components/Copy";
import { ConfirmDialog } from "../components/Dialog";
import { Empty, ErrorBox, Loading } from "../components/Status";
import { VerdictBadge } from "../components/Verdict";
import { short, shellQuote } from "../lib/format";

const STATE: Record<string, string> = { OPEN: "Open", MERGED: "Merged", CLOSED: "Closed" };

function RunLinks({ ids, decisions }: { ids: string[]; decisions: Map<string, string | null | undefined> }) {
  return (
    <ul className="run-links">
      {ids.map((id) => {
        const d = decisions.get(id);
        return (
          <li key={id}>
            <VerdictBadge decision={d === "PASS" || d === "REVIEW" || d === "BLOCK" ? d : null} />
            <Link to={routes.run(id)} className="mono">
              {id}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

export function PullRequestsPage() {
  const session = useSession();
  const { query, navigate } = useRouter();
  const [refresh, setRefresh] = useState(false);
  const prs = useApi<PullRequests>(session?.prs ? paths.pullRequests(refresh) : null);
  // Decisions for the run links; each is proof.json's recorded verdict.
  const runs = useApi<RunPage>(session?.prs ? paths.runs(new URLSearchParams({ limit: "500", sort: "recent" })) : null);
  const [text, setText] = useState("");
  const [asking, setAsking] = useState(false);
  const [posting, setPosting] = useState(false);
  const [failure, setFailure] = useState("");

  const decisions = useMemo(
    () => new Map((runs.data?.runs ?? []).map((r) => [r.id, r.decision] as const)),
    [runs.data],
  );

  if (!session) return <Loading what="the session" />;
  if (!session.prs) {
    return (
      <Empty title="Pull requests are not configured">
        Start the dashboard with a repository: <code>proofmerge dashboard runs --repo path/to/repo</code>. It lists
        pull requests with the GitHub CLI (<code>gh</code>).
      </Empty>
    );
  }
  if (prs.error && !prs.data) {
    return (
      <ErrorBox onRetry={prs.reload}>
        Pull requests are not available: {prs.error}
      </ErrorBox>
    );
  }
  if (!prs.data) return <Loading what="pull requests" />;
  const job = prs.data.job;
  const running = job?.status === "running";
  const q = text.trim().toLowerCase();
  const list = prs.data.prs.filter(
    (p) => !q || `#${p.number} ${p.title} ${p.author} ${p.head}`.toLowerCase().includes(q),
  );
  const pr: PullRequest | undefined =
    prs.data.prs.find((p) => p.number === Number(query.get("pr"))) ?? list[0];

  const start = async () => {
    if (!pr) return;
    setAsking(false);
    setFailure("");
    setPosting(true);
    try {
      const started = await startVerification(session, pr.number);
      navigate(routes.verification(started.job_id));
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPosting(false);
    }
  };

  const command = pr
    ? `git fetch origin +refs/pull/${pr.number}/head:refs/proofmerge/pr-${pr.number} ${shellQuote(`+refs/heads/${pr.base}:refs/proofmerge/base-${pr.number}`)}\n` +
      `proofmerge verify . --base refs/proofmerge/base-${pr.number} --head refs/proofmerge/pr-${pr.number} --intent-file intent.md --evidence-dir runs/pr-${pr.number}`
    : "";

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Pull requests</h1>
        <p className="muted">One verification runs at a time. The PR's code runs only in the Docker sandbox.</p>
      </div>
      {job ? (
        <div className={`panel job-card job-${job.status}`} role="status">
          <strong>
            PR #{job.pr_number}: {running ? job.step : job.status === "done" ? "verified" : "verification failed"}
          </strong>
          <Link className="btn" to={routes.verification(job.job_id)}>
            {running ? "Watch live" : "See the timeline"}
          </Link>
        </div>
      ) : null}
      <div className="filters" role="search">
        <input
          className="input grow"
          type="search"
          placeholder="Search pull requests"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Search pull requests"
        />
        <button
          type="button"
          className="btn"
          onClick={() => (refresh ? prs.reload() : setRefresh(true))}
          disabled={prs.loading}
        >
          {prs.loading ? "Loading…" : "Reload from GitHub"}
        </button>
        <span className="muted">
          {list.length} of {prs.data.prs.length}
        </span>
      </div>
      {prs.error ? <p className="muted">Could not refresh: {prs.error}. Showing the last result.</p> : null}
      <div className="split">
        <div className="panel table-wrap">
          {list.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">PR</th>
                  <th scope="col">Title</th>
                  <th scope="col">ProofMerge</th>
                </tr>
              </thead>
              <tbody>
                {list.map((p) => {
                  const latest = p.runs.at(-1);
                  const d = latest ? decisions.get(latest) : undefined;
                  return (
                    <tr key={p.number} aria-selected={pr?.number === p.number}>
                      <td className="mono">#{p.number}</td>
                      <td>
                        <Link to={routes.pullRequests(p.number)} className="row-link">
                          {p.title}
                        </Link>
                        <div className="sub">
                          {p.draft ? "Draft · " : ""}
                          {STATE[p.state] ?? p.state} · by {p.author}
                        </div>
                      </td>
                      <td>
                        {running && job?.pr_number === p.number ? (
                          <span className="badge">RUNNING</span>
                        ) : d ? (
                          <VerdictBadge decision={d} />
                        ) : (
                          <span className="muted">not verified</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <Empty title="No pull request matches">Change the search.</Empty>
          )}
        </div>
        {pr ? (
          <aside className="panel detail" aria-labelledby="pr-h">
            <h2 id="pr-h">
              #{pr.number} {pr.title}
            </h2>
            <dl className="facts">
              <dt>State</dt>
              <dd>
                {pr.draft ? "Draft, " : ""}
                {STATE[pr.state] ?? pr.state}
              </dd>
              <dt>Author</dt>
              <dd>{pr.author}</dd>
              <dt>Branch</dt>
              <dd>
                <code>{pr.head}</code> → <code>{pr.base}</code>
              </dd>
              <dt>Head</dt>
              <dd>
                <code>{short(pr.head_sha)}</code> <CopyButton text={pr.head_sha} label="head SHA" />
              </dd>
            </dl>
            <div className="actions">
              <button
                type="button"
                className="btn btn-primary"
                disabled={running || posting || pr.state !== "OPEN"}
                onClick={() => setAsking(true)}
              >
                {running ? `Verifying PR #${job?.pr_number}…` : posting ? "Starting…" : "Verify this PR"}
              </button>
              <a className="btn" href={pr.url} target="_blank" rel="noopener noreferrer">
                Open on GitHub
              </a>
            </div>
            {pr.state !== "OPEN" ? (
              <p className="muted">
                This pull request is {(STATE[pr.state] ?? pr.state).toLowerCase()}, so its base already contains it.
                Only open pull requests can be verified.
              </p>
            ) : null}
            {failure ? <ErrorBox>{failure}</ErrorBox> : null}
            <h3>Runs at this commit</h3>
            {pr.runs.length ? (
              <RunLinks ids={pr.runs} decisions={decisions} />
            ) : (
              <p className="muted">
                Not verified at <code>{short(pr.head_sha)}</code> yet.
              </p>
            )}
            {pr.older_runs.length ? (
              <>
                <h3>Earlier commits</h3>
                <p className="muted">The PR changed after these runs, so they may not match its code now.</p>
                <RunLinks ids={pr.older_runs} decisions={decisions} />
              </>
            ) : null}
            <details>
              <summary>Description (used as the intent)</summary>
              {pr.body ? <pre>{pr.body}</pre> : <p className="muted">No description; the intent is the title only.</p>}
            </details>
            <details>
              <summary>Run it yourself</summary>
              <pre>{command}</pre>
              <CopyButton text={command} label="commands" />
            </details>
          </aside>
        ) : null}
      </div>
      {asking && pr ? (
        <ConfirmDialog
          title={`Verify PR #${pr.number}?`}
          confirm={`Verify PR #${pr.number}`}
          onConfirm={() => void start()}
          onCancel={() => setAsking(false)}
        >
          <p>
            ProofMerge fetches the pull request into <code>refs/proofmerge/</code> without touching your branches,
            runs its code only in the Docker sandbox, and makes a bounded number of model calls.
          </p>
          <p className="note">Only verify pull requests from repositories you trust. The description is used as the intent.</p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
