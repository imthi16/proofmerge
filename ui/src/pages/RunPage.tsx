import { useState } from "react";
import { paths } from "../api/client";
import type { Hypothesis, RunDetail } from "../api/types";
import { useApi } from "../api/useApi";
import { Link, routes, useRouter } from "../app/router";
import { CopyButton } from "../components/Copy";
import { ExecutionGrid, LogView } from "../components/Executions";
import { Seal } from "../components/Seal";
import { ErrorBox, Loading } from "../components/Status";
import { VerdictBadge, VerdictHeadline } from "../components/Verdict";
import { short } from "../lib/format";

function Reasons({ title, items, className = "" }: { title: string; items: string[]; className?: string }) {
  if (!items.length) return null;
  return (
    <div className={className}>
      <h3>{title}</h3>
      <ul className="reasons">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function HypothesisDetail({ h, detail }: { h: Hypothesis; detail: RunDetail }) {
  const [open, setOpen] = useState("");
  const { proof } = detail;
  const experiment = proof.experiments.find((e) => e.hypothesis_id === h.id);
  const finding = proof.findings.find((f) => f.hypothesis_id === h.id);
  const claim = proof.intent.claims.find((c) => c.id === h.claim_id);
  const failure = detail.failures[h.id] ?? [];
  const probe = detail.probes[h.id];
  return (
    <article className="hyp" aria-labelledby={`hyp-${h.id}`}>
      <h3 id={`hyp-${h.id}`}>
        <code>{h.id}</code> {h.title}
      </h3>
      <p className="meta">
        cites <code>{h.claim_id}</code> {claim ? <span className={`kind kind-${claim.kind.toLowerCase()}`}>{claim.kind}</span> : "unknown claim"}
        {" · "}risk: {h.risk_category}
        {h.possibly_intended ? " · possibly intended" : ""}
      </p>
      {claim?.source_quote ? <blockquote>{claim.source_quote}</blockquote> : null}
      {proof.mismatched_probe_ids.includes(h.id) ? (
        <p className="note">Muse Wonder: the probe may not test this hypothesis.</p>
      ) : null}
      {proof.explained_probe_ids.includes(h.id) ? (
        <p className="note">
          Muse Wonder: this may be the intended change {proof.explained_probe_claim_ids[h.id] ?? ""}.
        </p>
      ) : null}
      <p>{h.suspected_behavior}</p>
      <dl className="expect">
        <dt>Expected on base</dt>
        <dd>{h.expected_base}</dd>
        <dt>Expected on head</dt>
        <dd>{h.expected_head}</dd>
        <dt>Falsified if</dt>
        <dd>{h.falsification_criteria}</dd>
      </dl>
      {h.changed_locations.length ? (
        <p className="meta">
          Changed locations: {h.changed_locations.map((loc) => <code key={loc}>{loc}</code>)}
        </p>
      ) : null}
      <ExecutionGrid experiment={experiment} open={open} onOpen={setOpen} />
      {open ? (
        <LogView log={detail.logs[`${h.id}:${open}`]} label={open.replace(":", " attempt ")} />
      ) : experiment?.executions.length ? (
        <p className="muted hint">Choose a result to see that attempt's log.</p>
      ) : null}
      <p className="meta">
        Classification: <strong>{finding ? finding.classification : "no finding recorded"}</strong>
        {finding?.verified ? " (verified)" : ""}
      </p>
      {failure.length ? (
        <details open>
          <summary>Why head failed</summary>
          <pre>{failure.join("\n")}</pre>
        </details>
      ) : null}
      <details>
        <summary>Probe source</summary>
        {probe ? <pre>{probe}</pre> : <p className="muted">Probe source not captured.</p>}
      </details>
      {finding ? (
        <details>
          <summary>Reproduce</summary>
          <pre>{finding.reproducer_command}</pre>
          <CopyButton text={finding.reproducer_command} label="reproducer command" />
        </details>
      ) : null}
    </article>
  );
}

export function RunPage({ runId }: { runId: string }) {
  const { data, error, reload } = useApi<RunDetail>(paths.run(runId));
  const { query, navigate } = useRouter();
  if (error) {
    return (
      <div className="stack">
        <Link to={routes.runs()}>← All runs</Link>
        <ErrorBox onRetry={reload}>
          Could not load run <code>{runId}</code>: {error}
        </ErrorBox>
      </div>
    );
  }
  if (!data) return <Loading what={`run ${runId}`} />;
  const { proof, summary } = data;
  const verdict = proof.verdict; // shown as recorded by verdict.decide(); never recomputed here
  const usage = proof.model_usage;
  const hypotheses = proof.hypotheses.hypotheses;
  const selectedId = query.get("h") ?? hypotheses[0]?.id ?? "";
  const selected = hypotheses.find((h) => h.id === selectedId) ?? hypotheses[0];
  const findingFor = (id: string) => proof.findings.find((f) => f.hypothesis_id === id);
  const models = Object.entries(usage.models)
    .map(([role, model]) => `${role}: ${model || "CLI default"}`)
    .join(", ");

  return (
    <div className="stack">
      <Link to={routes.runs()}>← All runs</Link>
      <section className={`panel verdict verdict-${verdict.decision.toLowerCase()}`} aria-labelledby="verdict-h">
        <div className="verdict-top">
          <VerdictBadge decision={verdict.decision} large />
          <div>
            <h1 id="verdict-h" className="run-title">
              {proof.run_id}
            </h1>
            <VerdictHeadline decision={verdict.decision} />
          </div>
        </div>
        <Reasons title="Why" items={verdict.reasons} />
        <Reasons title="Unresolved risks" items={verdict.unresolved_risks} />
        <Reasons title="Scope limitations" items={verdict.scope_limitations} className="muted-block" />
      </section>

      <section className="panel" aria-label="Run facts">
        <dl className="facts">
          <dt>Evidence</dt>
          <dd>
            <Seal integrity={summary.integrity} />
            {summary.integrity?.status === "changed" ? (
              <ul className="reasons warn">
                {summary.integrity.problems.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            ) : null}
          </dd>
          <dt>Base</dt>
          <dd>
            <code>{short(proof.base_sha)}</code> <CopyButton text={proof.base_sha} label="base SHA" />
          </dd>
          <dt>Head</dt>
          <dd>
            <code>{short(proof.head_sha)}</code> <CopyButton text={proof.head_sha} label="head SHA" />
          </dd>
          <dt>Language</dt>
          <dd>{proof.language}</dd>
          <dt>Model use</dt>
          <dd>
            {usage.calls} calls, {(usage.prompt_tokens + usage.completion_tokens).toLocaleString()} tokens
            {usage.endpoint ? (
              <div className="muted">
                {models} via <code>{usage.endpoint}</code>
              </div>
            ) : null}
          </dd>
          <dt>Orchestrator</dt>
          <dd>
            {proof.orchestration ? (
              <>
                {proof.orchestration.engine}
                {proof.orchestration.langgraph_version ? ` (LangGraph ${proof.orchestration.langgraph_version})` : ""}
                {proof.orchestration.retries.length ? (
                  <ul className="reasons">
                    {proof.orchestration.retries.map((r, i) => (
                      <li key={i}>retried: {r}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : (
              <span className="muted">not recorded (older proof)</span>
            )}
          </dd>
          {summary.benchmark ? (
            <>
              <dt>Benchmark label</dt>
              <dd>
                {summary.benchmark.label}
                {summary.benchmark.held_out ? ", held out" : ""}
                {summary.label_disagrees ? <strong className="warn"> · disagrees with the verdict</strong> : null}
              </dd>
            </>
          ) : null}
        </dl>
        <div className="actions">
          {summary.has_report ? (
            <a className="btn btn-primary" href={paths.report(runId)} target="_blank" rel="noopener noreferrer">
              Open static report
            </a>
          ) : null}
          <span className="cmdline">
            <code>{data.replay_command}</code> <CopyButton text={data.replay_command} label="replay command" />
          </span>
        </div>
      </section>

      <section className="panel" aria-labelledby="hyps-h">
        <h2 id="hyps-h">Hypotheses and probes</h2>
        {hypotheses.length && selected ? (
          <div className="explorer">
            <ul className="hyp-list" aria-label="Hypotheses">
              {hypotheses.map((h) => {
                const finding = findingFor(h.id);
                const next = new URLSearchParams(query);
                next.set("h", h.id);
                return (
                  <li key={h.id}>
                    <button
                      type="button"
                      aria-pressed={h.id === selected.id}
                      onClick={() => navigate(`${routes.run(runId)}?${next}`, { replace: true })}
                    >
                      <code>{h.id}</code>
                      <span className={`cls cls-${(finding?.classification ?? "none").toLowerCase()}`}>
                        {finding?.classification.replaceAll("_", " ") ?? "no finding"}
                        {finding?.verified ? " ✓" : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <HypothesisDetail key={selected.id} h={selected} detail={data} />
          </div>
        ) : (
          <p className="muted">No hypotheses were recorded for this run.</p>
        )}
        {proof.hypotheses.rejected.length ? (
          <details>
            <summary>{proof.hypotheses.rejected.length} rejected hypothesis proposal(s)</summary>
            <ul className="reasons">
              {proof.hypotheses.rejected.map((r, i) => (
                <li key={i}>{r.reason}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </section>

      <section className="panel" aria-labelledby="tamper-h">
        <h2 id="tamper-h">Test-tamper guard</h2>
        {proof.tamper_findings.length ? (
          <ul className="reasons">
            {proof.tamper_findings.map((t, i) => (
              <li key={i}>
                <code>{t.test_path}</code>
                {t.test_name ? (
                  <>
                    {" "}
                    :: <code>{t.test_name}</code>
                  </>
                ) : null}{" "}
                — {t.kind}
                {t.base_test_on_head_outcome ? `, base test on head: ${t.base_test_on_head_outcome}` : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No tamper finding was recorded.</p>
        )}
      </section>

      <section className="panel" aria-labelledby="claims-h">
        <h2 id="claims-h">Intent claims</h2>
        {proof.intent.claims.map((c) => (
          <div key={c.id} className="claim">
            <code>{c.id}</code> <span className={`kind kind-${c.kind.toLowerCase()}`}>{c.kind}</span>
            {proof.contested_claim_ids.includes(c.id) ? <span className="kind"> contested by Muse Wonder</span> : null}
            <div>{c.statement}</div>
            {c.source_quote ? (
              <blockquote>{c.source_quote}</blockquote>
            ) : (
              <div className="muted">Implicit claim — not stated in the intent text.</div>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}
