// ProofMerge dashboard. Every string from a run is untrusted evidence: it is rendered as
// React text (escaped), never through dangerouslySetInnerHTML.
(function () {
  "use strict";
  const { useState, useEffect, useMemo, useCallback, useRef } = React;
  const html = htm.bind(React.createElement);
  const DECISIONS = ["BLOCK", "REVIEW", "PASS"];
  const HEADLINE = {
    BLOCK: "Verified regression — do not merge",
    REVIEW: "Not conclusive — a human must review",
    PASS: "No regression found within the tested scope",
  };

  const store = {
    get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private window */ } },
  };

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    return { tab: params.get("tab") || "runs", run: params.get("run") || "", pr: params.get("pr") || "" };
  }
  function writeHash(state) {
    const params = new URLSearchParams();
    if (state.tab !== "runs") params.set("tab", state.tab);
    if (state.run) params.set("run", state.run);
    if (state.pr) params.set("pr", state.pr);
    history.replaceState(null, "", "#" + params.toString());
  }

  async function getJSON(url) {
    const response = await fetch(url, { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  const short = (sha) => (sha || "").slice(0, 7);
  // A benchmark label disagrees when a faulty change is not blocked or a safe one is.
  function disagrees(run) {
    if (!run.benchmark || !run.decision) return false;
    return run.benchmark.label === "BLOCK" ? run.decision !== "BLOCK" : run.decision === "BLOCK";
  }

  function Copy({ text, label }) {
    const [done, setDone] = useState(false);
    const copy = () => navigator.clipboard?.writeText(text).then(() => {
      setDone(true); setTimeout(() => setDone(false), 1200);
    });
    return html`<button class="copy" type="button" onClick=${copy}
      aria-label=${"Copy " + label}>${done ? "copied" : "copy"}</button>`;
  }

  function Seal({ integrity }) {
    const text = { intact: "evidence intact", changed: "evidence changed", unsealed: "no manifest" };
    return html`<span class=${"seal " + integrity.status}>${text[integrity.status]}</span>`;
  }

  function Distribution({ runs, filter, setFilter }) {
    const counts = Object.fromEntries(DECISIONS.map((d) => [d, runs.filter((r) => r.decision === d).length]));
    const total = runs.length;
    const unreadable = runs.filter((r) => r.error).length;
    return html`<section class="dist" aria-label="Verdicts">
      <div class=${"dist-bar" + (filter ? " filtered" : "")}>
        ${DECISIONS.filter((d) => counts[d]).map((d) => html`
          <button key=${d} type="button" class=${"seg " + d.toLowerCase()} style=${{ flexGrow: counts[d] }}
            aria-pressed=${filter === d} onClick=${() => setFilter(filter === d ? "" : d)}
            title=${filter === d ? "Show every verdict" : `Show only ${d}`}>
            <span class="k">${d}</span><span class="n">${counts[d]}</span>
          </button>`)}
      </div>
      <div class="dist-legend muted">
        <span>${total} run${total === 1 ? "" : "s"}${unreadable ? `, ${unreadable} unreadable` : ""}</span>
        <span>${filter ? html`Showing ${filter} only. <button class="copy" type="button"
          onClick=${() => setFilter("")}>clear</button>` : "Click a verdict to filter"}</span>
      </div>
    </section>`;
  }

  const SORTS = {
    verdict: (a, b) => DECISIONS.indexOf(a.decision) - DECISIONS.indexOf(b.decision) || a.id.localeCompare(b.id),
    regressions: (a, b) => (b.regressions || 0) - (a.regressions || 0),
    calls: (a, b) => (b.calls || 0) - (a.calls || 0),
    name: (a, b) => a.id.localeCompare(b.id),
  };

  function RunsTable({ runs, selected, onSelect, sort, setSort }) {
    const body = useRef(null);
    const onKey = (event) => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      event.preventDefault();
      const index = runs.findIndex((r) => r.id === selected);
      const next = runs[Math.max(0, Math.min(runs.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
      if (next) onSelect(next.id);
    };
    // On two-column layouts the list scrolls on its own, so keep the selected row in it visible.
    useEffect(() => {
      if (!matchMedia("(min-width: 64.01rem)").matches) return;
      body.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    }, [selected, runs.length]);
    const head = (key, label, cls = "") => html`<th class=${cls}><button type="button"
      aria-sort=${sort === key ? "descending" : undefined} onClick=${() => setSort(key)}>${label}</button></th>`;
    return html`<div class="panel runs">
      <table>
        <thead><tr>
          ${head("verdict", "Verdict")}${head("name", "Run")}
          <th class="hide-sm">Base → head</th>
          ${head("regressions", "Regressions", "num")}${head("calls", "Calls", "num hide-sm")}
        </tr></thead>
        <tbody ref=${body} tabIndex="0" onKeyDown=${onKey} aria-label="Runs; use arrow keys to move">
          ${runs.map((run) => html`
            <tr key=${run.id} aria-selected=${run.id === selected} onClick=${() => onSelect(run.id)}>
              <td>${run.error ? html`<span class="pill">ERROR</span>` : html`<span class=${"pill " + run.decision}>${run.decision}</span>`}</td>
              <td>
                <div class="run-name">${run.id}</div>
                <div class="run-sub">
                  ${run.integrity ? html`<${Seal} integrity=${run.integrity} />` : null}
                  ${run.benchmark ? html`, label ${run.benchmark.label}${run.benchmark.held_out ? ", held out" : ""}${disagrees(run)
                    ? html`, <span class="disagree">disagrees</span>` : null}` : null}
                  ${run.error ? run.error : null}
                </div>
              </td>
              <td class="hide-sm mono">${short(run.base_sha)} → ${short(run.head_sha)}</td>
              <td class="num">${run.regressions ?? "—"}</td>
              <td class="num hide-sm">${run.calls ?? "—"}</td>
            </tr>`)}
        </tbody>
      </table>
    </div>`;
  }

  function Grid({ experiment, open, onOpen }) {
    if (!experiment || !experiment.executions.length) return html`<p class="muted">No probe was run for this hypothesis.</p>`;
    const attempts = [...new Set(experiment.executions.map((e) => e.attempt))].sort((a, b) => a - b).slice(0, 3);
    return html`<div class="grid" role="group" aria-label="Probe outcomes per attempt">
      <span></span>${attempts.map((a) => html`<span key=${a} class="muted" style=${{ fontSize: ".78rem", textAlign: "center" }}>attempt ${a}</span>`)}
      ${["base", "head"].map((revision) => {
        const runs = experiment.executions.filter((e) => e.revision === revision);
        return html`<${React.Fragment} key=${revision}>
          <span class="rev">${revision} <code>${short(runs[0]?.commit_sha)}</code></span>
          ${attempts.map((a) => {
            const outcome = runs.find((e) => e.attempt === a)?.outcome || "—";
            const key = `${revision}:${a}`;
            return html`<button key=${a} type="button" class=${"cell " + outcome} aria-pressed=${open === key}
              onClick=${() => onOpen(open === key ? "" : key)} aria-label=${`${revision} attempt ${a}: ${outcome}. Show its log`}>${outcome}</button>`;
          })}
        </${React.Fragment}>`;
      })}
    </div>`;
  }

  function LogView({ log, label }) {
    if (!log) return html`<p class="muted">No log was recorded for ${label}.</p>`;
    return html`<div class="log">
      <div class="log-head"><strong>${label}</strong>
        <span class="muted">${log.outcome}, exit ${log.exit_code ?? "none (timed out)"}, ${(log.duration_ms / 1000).toFixed(1)} s</span></div>
      <pre class="cmd">${log.command.join(" ")}</pre>
      <pre>${log.stdout || "(no stdout)"}</pre>
      ${log.stderr ? html`<details><summary>stderr</summary><pre>${log.stderr}</pre></details>` : null}
    </div>`;
  }

  function Hypothesis({ h, proof, probe, failure, logs }) {
    const [open, setOpen] = useState("");
    const experiment = proof.experiments.find((e) => e.hypothesis_id === h.id);
    const finding = proof.findings.find((f) => f.hypothesis_id === h.id);
    const claim = proof.intent.claims.find((c) => c.id === h.claim_id);
    return html`<article class="hyp">
      <h3><code>${h.id}</code> ${h.title}</h3>
      <p class="meta">cites <code>${h.claim_id}</code> ${claim ? claim.kind : "unknown claim"}, risk: ${h.risk_category}${h.possibly_intended ? ", possibly intended" : ""}</p>
      ${claim?.source_quote ? html`<blockquote>${claim.source_quote}</blockquote>` : null}
      ${proof.mismatched_probe_ids.includes(h.id) ? html`<p class="note">Muse Wonder: probe may not test this hypothesis</p>` : null}
      ${proof.explained_probe_ids.includes(h.id) ? html`<p class="note">Muse Wonder: may be the intended change ${proof.explained_probe_claim_ids[h.id] || ""}</p>` : null}
      <p>${h.suspected_behavior}</p>
      <p class="expect"><strong>Expected on base:</strong> ${h.expected_base}</p>
      <p class="expect"><strong>Expected on head:</strong> ${h.expected_head}</p>
      <${Grid} experiment=${experiment} open=${open} onOpen=${setOpen} />
      ${open ? html`<${LogView} log=${logs[`${h.id}:${open}`]} label=${open.replace(":", " attempt ")} />`
        : experiment?.executions.length ? html`<p class="hint muted">Click a result to see that attempt's log.</p>` : null}
      <p class="meta">classification: <strong>${finding ? finding.classification : "no finding recorded"}</strong>${finding?.verified ? " (verified)" : ""}</p>
      ${failure?.length ? html`<details open><summary>Why head failed</summary><pre>${failure.join("\n")}</pre></details>` : null}
      <details><summary>Probe source</summary>${probe ? html`<pre>${probe}</pre>` : html`<p class="muted">Probe source not captured.</p>`}</details>
      ${finding ? html`<details><summary>Reproduce</summary><pre>${finding.reproducer_command}</pre></details>` : null}
    </article>`;
  }

  function Detail({ id }) {
    const [state, setState] = useState({ loading: true });
    useEffect(() => {
      if (!id) return;
      let live = true;
      setState({ loading: true });
      getJSON("/api/run?id=" + encodeURIComponent(id))
        .then((data) => live && setState({ data }))
        .catch((error) => live && setState({ error: error.message }));
      return () => { live = false; };
    }, [id]);
    if (!id) return html`<aside class="panel detail empty"><strong>Pick a run</strong>Its verdict, probes and evidence show here.</aside>`;
    if (state.error) return html`<aside class="panel detail"><div class="error-box">Could not load this run: ${state.error}</div></aside>`;
    if (state.loading) return html`<aside class="panel detail muted">Loading ${id}…</aside>`;
    const { summary, proof, probes, failures, logs } = state.data;
    const decision = proof.verdict.decision;
    const usage = proof.model_usage;
    const models = Object.entries(usage.models || {}).map(([role, model]) => `${role} ${model || "CLI default"}`).join(", ");
    // The id is relative to the dashboard folder, so the command uses the absolute path.
    const replay = `proofmerge replay '${summary.proof_path.replaceAll("'", "'\\''")}'`;
    return html`<aside class="panel detail" aria-label=${"Run " + id}>
      <div class="detail-head">
        <span class=${"decision " + decision}>${decision}</span>
        <strong>${HEADLINE[decision]}</strong>
      </div>
      <ul class="reasons">${proof.verdict.reasons.map((r, i) => html`<li key=${i}>${r}</li>`)}</ul>
      <dl class="facts">
        <dt>run</dt><dd><code>${proof.run_id}</code><${Copy} text=${location.href} label="link to this run" /></dd>
        <dt>evidence</dt><dd><${Seal} integrity=${summary.integrity} />
          ${summary.integrity.problems.length && summary.integrity.status !== "unsealed"
            ? html`<span class="muted">${summary.integrity.problems.length} problem(s)</span>` : null}</dd>
        <dt>base</dt><dd><code>${short(proof.base_sha)}</code><${Copy} text=${proof.base_sha} label="base SHA" /></dd>
        <dt>head</dt><dd><code>${short(proof.head_sha)}</code><${Copy} text=${proof.head_sha} label="head SHA" /></dd>
        <dt>model calls</dt><dd>${usage.calls} calls, ${usage.prompt_tokens + usage.completion_tokens} tokens</dd>
        ${usage.endpoint ? html`<dt>models</dt><dd>${models} via <code>${usage.endpoint}</code></dd>` : null}
        ${summary.benchmark ? html`<dt>label</dt><dd>${summary.benchmark.label}${summary.benchmark.held_out ? ", held out" : ""}
          ${disagrees(summary) ? html`<span class="disagree">disagrees with the verdict</span>` : null}</dd>` : null}
      </dl>
      ${summary.integrity.status === "changed" ? html`<ul class="reasons">${summary.integrity.problems.map((p, i) => html`<li key=${i} class="disagree">${p}</li>`)}</ul>` : null}
      <div class="actions">
        ${summary.has_report ? html`<a class="ghost" href=${"/report?id=" + encodeURIComponent(id)} target="_blank" rel="noopener">Open full report</a>` : null}
        <span class="ghost" style=${{ cursor: "default" }}><code>${replay}</code> <${Copy} text=${replay} label="replay command" /></span>
      </div>
      <h2>Hypotheses and probes</h2>
      ${proof.hypotheses.hypotheses.length ? proof.hypotheses.hypotheses.map((h) => html`
        <${Hypothesis} key=${h.id} h=${h} proof=${proof} probe=${probes[h.id]} failure=${failures[h.id]} logs=${logs || {}} />`)
        : html`<p class="muted">No hypotheses were recorded for this run.</p>`}
      <h2>Test-tamper guard</h2>
      ${proof.tamper_findings.length ? html`<ul class="reasons">${proof.tamper_findings.map((t, i) => html`<li key=${i}>
        <code>${t.test_path}</code>${t.test_name ? html` :: <code>${t.test_name}</code>` : null} — ${t.kind}${t.base_test_on_head_outcome ? `, base test on head: ${t.base_test_on_head_outcome}` : ""}</li>`)}</ul>`
        : html`<p class="muted">No tamper finding was recorded.</p>`}
      <h2>Intent claims</h2>
      ${proof.intent.claims.map((c) => html`<div key=${c.id} style=${{ marginBottom: ".6rem" }}>
        <code>${c.id}</code> <span class=${"kind " + c.kind}>${c.kind}</span>
        ${proof.contested_claim_ids.includes(c.id) ? html` <span class="kind">contested by Muse Wonder</span>` : null}
        <div>${c.statement}</div>
        ${c.source_quote ? html`<blockquote>${c.source_quote}</blockquote>` : html`<div class="muted">implicit claim — not stated in the intent text</div>`}
      </div>`)}
    </aside>`;
  }

  function RunsView({ index, selected, onSelect }) {
    const [filter, setFilter] = useState("");
    const [query, setQuery] = useState("");
    const [onlyDisagree, setOnlyDisagree] = useState(false);
    const [seal, setSeal] = useState("");
    const [set, setSet] = useState("");
    const sets = [...new Set(index.runs.filter((r) => r.id.includes("/")).map((r) => r.id.split("/")[0]))].sort().reverse();
    const [sort, setSort] = useState("verdict");
    const search = useRef(null);
    useEffect(() => {
      const onKey = (event) => {
        if (event.key === "/" && document.activeElement !== search.current) {
          event.preventDefault(); search.current?.focus();
        }
      };
      addEventListener("keydown", onKey);
      return () => removeEventListener("keydown", onKey);
    }, []);
    // On one-column layouts the detail sits below the list, so bring it into view.
    const pick = (id) => {
      onSelect(id);
      if (matchMedia("(max-width: 64rem)").matches) {
        requestAnimationFrame(() => document.querySelector(".detail")?.scrollIntoView({ block: "start" }));
      }
    };
    const hasLabels = index.runs.some((r) => r.benchmark);
    const runs = useMemo(() => {
      const q = query.trim().toLowerCase();
      return index.runs
        .filter((r) => !filter || r.decision === filter)
        .filter((r) => !seal || r.integrity?.status === seal)
        .filter((r) => !set || r.id.startsWith(set + "/"))
        .filter((r) => !onlyDisagree || disagrees(r))
        .filter((r) => !q || [r.id, r.run_id, ...(r.reasons || [])].join(" ").toLowerCase().includes(q))
        .sort(SORTS[sort]);
    }, [index, filter, query, seal, set, onlyDisagree, sort]);
    const exportCsv = () => {
      const cols = ["id", "decision", "regressions", "hypotheses", "calls", "tokens", "integrity", "label", "held_out", "disagrees", "base_sha", "head_sha"];
      const cell = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
      const rows = runs.map((r) => [r.id, r.decision || "ERROR", r.regressions, r.hypotheses, r.calls, r.tokens,
        r.integrity?.status, r.benchmark?.label, r.benchmark?.held_out, disagrees(r), r.base_sha, r.head_sha].map(cell).join(","));
      const url = URL.createObjectURL(new Blob([[cols.join(","), ...rows].join("\n") + "\n"], { type: "text/csv" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: `proofmerge-runs-${index.root}.csv` });
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    if (!index.runs.length) {
      return html`<div class="panel empty"><strong>No runs in this folder yet</strong>
        Run <code>proofmerge verify … --evidence-dir ${index.root}/my-run</code>, then press Refresh.</div>`;
    }
    return html`
      <${Distribution} runs=${index.runs} filter=${filter} setFilter=${setFilter} />
      <div class="filters">
        <input ref=${search} class="search" type="search" placeholder="Search runs and reasons  ( / )"
          value=${query} onInput=${(e) => setQuery(e.target.value)} aria-label="Search runs" />
        ${sets.length > 1 ? html`<select class="select" value=${set} onChange=${(e) => setSet(e.target.value)} aria-label="Run set">
          <option value="">All run sets</option>${sets.map((x) => html`<option key=${x} value=${x}>${x}</option>`)}
        </select>` : null}
        <select class="select" value=${seal} onChange=${(e) => setSeal(e.target.value)} aria-label="Evidence integrity">
          <option value="">Any evidence state</option><option value="intact">Evidence intact</option>
          <option value="changed">Evidence changed</option><option value="unsealed">No manifest</option>
        </select>
        ${hasLabels ? html`<label class="check"><input type="checkbox" checked=${onlyDisagree}
          onChange=${(e) => setOnlyDisagree(e.target.checked)} /> Disagrees with label</label>` : null}
        <span class="muted" style=${{ fontSize: ".88rem" }}>${runs.length} shown</span>
        <button class="ghost" type="button" onClick=${exportCsv} disabled=${!runs.length}>Export CSV</button>
      </div>
      <div class="split">
        ${runs.length ? html`<${RunsTable} runs=${runs} selected=${selected} onSelect=${pick} sort=${sort} setSort=${setSort} />`
          : html`<div class="panel empty"><strong>No run matches</strong>Change the search or filters.</div>`}
        <${Detail} id=${selected} />
      </div>`;
  }

  function Matrix({ rows }) {
    const groups = [["BLOCK", "Faulty"], ["NOT_BLOCK", "Safe"]];
    const cols = ["BLOCK", "REVIEW", "PASS", "ERROR"];
    return html`<table class="matrix">
      <thead><tr><th>Label</th>${cols.map((c) => html`<th key=${c}>${c}</th>`)}<th>Total</th></tr></thead>
      <tbody>${groups.map(([label, name]) => {
        const group = rows.filter((r) => r.label === label);
        return html`<tr key=${label}><td>${name}</td>
          ${cols.map((c) => {
            const n = group.filter((r) => r.decision === c).length;
            const bad = n && ((label === "BLOCK" && c === "PASS") || (label === "NOT_BLOCK" && c === "BLOCK"));
            const good = n && ((label === "BLOCK" && c === "BLOCK") || (label === "NOT_BLOCK" && c === "PASS"));
            return html`<td key=${c} class=${bad ? "miss" : good ? "hit" : ""}>${n}</td>`;
          })}<td>${group.length}</td></tr>`;
      })}</tbody>
    </table>`;
  }

  function BenchmarksView({ index, onOpenRun }) {
    const known = new Set(index.runs.map((r) => r.id));
    const [slice, setSlice] = useState("all");
    const pick = (rows) => rows.filter((r) => slice === "all" || (slice === "held" ? r.held_out : !r.held_out));
    const runs = index.benchmarks.filter((b) => b.kind === "run");
    const baselines = index.benchmarks.filter((b) => b.kind === "baselines");
    if (!index.benchmarks.length) {
      return html`<div class="panel empty"><strong>No benchmark results here</strong>
        Point the dashboard at a folder holding a <code>results.json</code> from <code>bench/run_bench.py</code>, such as <code>bench/results</code>.</div>`;
    }
    const caught = (rows, key) => rows.filter((r) => r.label === "BLOCK" && r[key] === "BLOCK").length;
    const falseBlocks = (rows, key) => rows.filter((r) => r.label === "NOT_BLOCK" && r[key] === "BLOCK").length;
    return html`
      <div class="bench-head">
        <h2>Benchmarks</h2>
        <div class="seg-ctl" role="group" aria-label="Candidates">
          ${[["all", "All"], ["held", "Held out"], ["seen", "Not held out"]].map(([key, label]) => html`
            <button key=${key} type="button" aria-pressed=${slice === key} onClick=${() => setSlice(key)}>${label}</button>`)}
        </div>
        <span class="muted">A smoke benchmark on synthetic changes, not an accuracy claim.</span>
      </div>
      ${runs.slice().reverse().map((b) => {
        const rows = pick(b.candidates);
        const misses = rows.filter((r) => disagrees({ decision: r.decision, benchmark: r }));
        // Baselines count only when they ran on exactly the same candidates.
        const names = b.candidates.map((r) => r.name).sort().join();
        const base = baselines.find((x) => x.candidates.map((r) => r.name).sort().join() === names);
        const baseRows = base ? pick(base.candidates) : [];
        return html`<section key=${b.id} class="panel bench">
          <h3>${b.id}</h3>
          <p class="meta muted">${b.git_sha ? html`commit <code>${short(b.git_sha)}</code>, ` : null}${b.model || "model not recorded"}${b.date ? ", " + new Date(b.date).toLocaleString() : ""}</p>
          <${Matrix} rows=${rows} />
          ${base ? html`<h3 style=${{ marginTop: "1rem" }}>Against simple baselines</h3>
            <table class="matrix"><thead><tr><th>Method</th><th>Faulty blocked</th><th>Safe blocked</th></tr></thead>
              <tbody>
                <tr><td>Base tests on head (A)</td><td>${caught(baseRows, "a")} / ${baseRows.filter((r) => r.label === "BLOCK").length}</td><td>${falseBlocks(baseRows, "a")} / ${baseRows.filter((r) => r.label === "NOT_BLOCK").length}</td></tr>
                <tr><td>One-shot Spark opinion (B)</td><td>${caught(baseRows, "b")} / ${baseRows.filter((r) => r.label === "BLOCK").length}</td><td>${falseBlocks(baseRows, "b")} / ${baseRows.filter((r) => r.label === "NOT_BLOCK").length}</td></tr>
                <tr><td>ProofMerge (BLOCK needs base PASS 3/3, head FAIL 3/3)</td><td>${caught(rows, "decision")} / ${rows.filter((r) => r.label === "BLOCK").length}</td><td>${falseBlocks(rows, "decision")} / ${rows.filter((r) => r.label === "NOT_BLOCK").length}</td></tr>
              </tbody></table>
            <p class="muted" style=${{ fontSize: ".85rem" }}>Baselines from <code>${base.id}</code>. B gives an opinion with no reproducer; a ProofMerge BLOCK comes with a replayable probe.</p>` : null}
          ${misses.length ? html`<h3 style=${{ marginTop: "1rem" }}>Not matching the label</h3>
            <ul class="mismatch">${misses.map((r) => html`<li key=${r.name}>${known.has(`${b.id}/${r.name}`)
              ? html`<button class="link" type="button" onClick=${() => onOpenRun(`${b.id}/${r.name}`)}>${r.name}</button>`
              : html`<strong>${r.name}</strong>`}
              <span class="muted"> — label ${r.label}, got ${r.decision}${r.held_out ? ", held out" : ""}.</span> ${r.reasons[0] || ""}</li>`)}</ul>` : null}
        </section>`;
      })}`;
  }

  const STATE = { OPEN: "Open", MERGED: "Merged", CLOSED: "Closed" };

  function ConfirmVerify({ pr, onConfirm, onCancel }) {
    const cancel = useRef(null);
    useEffect(() => {
      cancel.current?.focus();
      const onKey = (event) => { if (event.key === "Escape") onCancel(); };
      addEventListener("keydown", onKey);
      return () => removeEventListener("keydown", onKey);
    }, []);
    return html`<div class="scrim" onClick=${(e) => e.target === e.currentTarget && onCancel()}>
      <div class="dialog panel" role="dialog" aria-modal="true" aria-labelledby="verify-title">
        <h3 id="verify-title">Verify PR #${pr.number}?</h3>
        <p>ProofMerge fetches the pull request into <code>refs/proofmerge/</code> without touching
          your branches, runs its code in the Docker sandbox, and makes up to 19 model calls.</p>
        <p class="note">Only verify pull requests from repositories you trust. The description is used as the intent.</p>
        <div class="actions">
          <button class="primary" type="button" onClick=${onConfirm}>Verify PR #${pr.number}</button>
          <button ref=${cancel} class="ghost" type="button" onClick=${onCancel}>Cancel</button>
        </div>
      </div>
    </div>`;
  }

  function JobPanel({ job, onOpenRun }) {
    if (!job) return null;
    const cls = { running: "running", done: "done", failed: "failed" }[job.status];
    return html`<div class=${"job " + cls} role="status" aria-live="polite">
      <strong>PR #${job.number}: ${job.status === "running" ? job.step : job.status === "done" ? "verified" : "failed"}</strong>
      ${job.status === "running" ? html`<span class="muted"> — the page updates when it finishes.</span>` : null}
      ${job.status === "failed" ? html`<div>${job.step}${job.exit_code != null ? ` (exit ${job.exit_code})` : ""}</div>` : null}
      ${job.status === "done" ? html`<div class="actions"><button class="ghost" type="button" onClick=${() => onOpenRun(job.run)}>Open the new run</button></div>` : null}
      ${job.output ? html`<details><summary>Output</summary><pre>${job.output}</pre></details>` : null}
    </div>`;
  }

  function PullsView({ index, token, selected, onSelect, onOpenRun, onRunsChanged }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [asking, setAsking] = useState(false);
    const [posting, setPosting] = useState("");
    const [tick, setTick] = useState(0);
    const [query, setQuery] = useState("");
    const lastStatus = useRef(null);
    const load = useCallback((refresh) => getJSON("/api/prs" + (refresh ? "?refresh=1" : ""))
      .then((d) => { setData(d); setError(""); })
      .catch((e) => { setError(e.message); setTick((t) => t + 1); }), []);
    useEffect(() => { load(false); }, [load]);
    const job = data?.job;
    useEffect(() => {
      if (lastStatus.current === "running" && job && job.status !== "running") onRunsChanged();
      lastStatus.current = job?.status;
      if (job?.status !== "running") return;
      const timer = setTimeout(() => load(false), 2000);
      return () => clearTimeout(timer);
    }, [data, tick]);
    if (error && !data) {
      return html`<div class="panel empty"><strong>Pull requests are not available</strong>${error}
        <div class="muted" style=${{ marginTop: ".5rem" }}>Start with <code>proofmerge dashboard ${index.root} --repo path/to/repo</code>.</div></div>`;
    }
    if (!data) return html`<p class="boot">Loading pull requests…</p>`;
    const verdictOf = (id) => index.runs.find((r) => r.id === id)?.decision;
    const q = query.trim().toLowerCase();
    const prs = data.prs.filter((p) => !q || `#${p.number} ${p.title} ${p.author} ${p.head}`.toLowerCase().includes(q));
    const pr = data.prs.find((p) => p.number === Number(selected)) || prs[0];
    const start = () => {
      setAsking(false); setPosting("");
      fetch("/api/verify", { method: "POST", headers: { "Content-Type": "application/json", "X-ProofMerge-Token": token },
        body: JSON.stringify({ number: pr.number }) })
        .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`); })
        .then(() => load(false)).catch((e) => setPosting(e.message));
    };
    const running = job?.status === "running";
    const command = pr ? `git fetch origin +refs/pull/${pr.number}/head:refs/proofmerge/pr-${pr.number} +refs/heads/${pr.base}:refs/proofmerge/base-${pr.number}\nproofmerge verify . --base refs/proofmerge/base-${pr.number} --head refs/proofmerge/pr-${pr.number} --intent-file intent.md --evidence-dir ${index.root}/pr-${pr.number}` : "";
    return html`
      <div class="filters">
        <input class="search" type="search" placeholder="Search pull requests" value=${query}
          onInput=${(e) => setQuery(e.target.value)} aria-label="Search pull requests" />
        <button class="ghost" type="button" onClick=${() => load(true)}>Reload from GitHub</button>
        <span class="muted" style=${{ fontSize: ".88rem" }}>${prs.length} of ${data.prs.length}</span>
      </div>
      ${error ? html`<p class="muted" style=${{ fontSize: ".88rem" }}>Could not refresh: ${error}. Showing the last result.</p>` : null}
      <${JobPanel} job=${job} onOpenRun=${onOpenRun} />
      <div class="split">
        <div class="panel runs">
          ${prs.length ? html`<table>
            <thead><tr><th>PR</th><th>Title</th><th class="hide-sm">Branch</th><th>ProofMerge</th></tr></thead>
            <tbody>${prs.map((p) => {
              const verdict = p.runs.length ? verdictOf(p.runs[p.runs.length - 1]) : null;
              return html`<tr key=${p.number} aria-selected=${pr && p.number === pr.number} onClick=${() => onSelect(p.number)}>
                <td class="mono">#${p.number}</td>
                <td><div class="run-name">${p.title}</div>
                  <div class="run-sub">${p.draft ? "Draft, " : ""}${STATE[p.state] || p.state}, by ${p.author}</div></td>
                <td class="hide-sm mono" style=${{ fontSize: ".8rem" }}>${p.head} → ${p.base}</td>
                <td>${verdict ? html`<span class=${"pill " + verdict}>${verdict}</span>`
                  : running && job.number === p.number ? html`<span class="pill">RUNNING</span>`
                  : html`<span class="muted" style=${{ fontSize: ".85rem" }}>not verified</span>`}</td>
              </tr>`;
            })}</tbody></table>` : html`<div class="empty"><strong>No pull request matches</strong>Change the search.</div>`}
        </div>
        ${pr ? html`<aside class="panel detail" aria-label=${"Pull request " + pr.number}>
          <div class="detail-head"><span class="decision" style=${{ fontSize: "1.6rem" }}>#${pr.number}</span><strong>${pr.title}</strong></div>
          <dl class="facts">
            <dt>state</dt><dd>${pr.draft ? "Draft, " : ""}${STATE[pr.state] || pr.state}</dd>
            <dt>author</dt><dd>${pr.author}</dd>
            <dt>branch</dt><dd><code>${pr.head}</code> → <code>${pr.base}</code></dd>
            <dt>head</dt><dd><code>${short(pr.head_sha)}</code><${Copy} text=${pr.head_sha} label="head SHA" /></dd>
          </dl>
          <div class="actions">
            <button class="primary" type="button" disabled=${running || pr.state !== "OPEN"} onClick=${() => setAsking(true)}
              title=${pr.state !== "OPEN" ? "Only open pull requests can be verified" : undefined}>
              ${running ? `Verifying PR #${job.number}…` : "Verify this PR"}</button>
            <a class="ghost" href=${pr.url} target="_blank" rel="noopener noreferrer">Open on GitHub</a>
          </div>
          ${pr.state !== "OPEN" ? html`<p class="muted" style=${{ fontSize: ".88rem" }}>This pull request is ${(STATE[pr.state] || pr.state).toLowerCase()}, so its base already contains it. Only open pull requests can be verified.</p>` : null}
          ${posting ? html`<div class="error-box" style=${{ marginTop: ".75rem" }}>${posting}</div>` : null}
          <h2>ProofMerge runs at this commit</h2>
          ${pr.runs.length ? html`<ul class="reasons">${pr.runs.map((id) => html`<li key=${id}>
              <span class=${"pill " + (verdictOf(id) || "")}>${verdictOf(id) || "?"}</span>
              <button class="copy" type="button" style=${{ marginLeft: ".4rem" }} onClick=${() => onOpenRun(id)}>open</button>
              <span class="mono" style=${{ marginLeft: ".4rem" }}>${id}</span></li>`)}</ul>`
            : html`<p class="muted">Not verified at <code>${short(pr.head_sha)}</code> yet.</p>`}
          ${pr.older_runs?.length ? html`<h2>Earlier commits</h2>
            <p class="muted" style=${{ fontSize: ".88rem" }}>The PR changed after these runs, so they may not match the code now.</p>
            <ul class="reasons">${pr.older_runs.map((id) => html`<li key=${id}>
              <span class=${"pill " + (verdictOf(id) || "")}>${verdictOf(id) || "?"}</span>
              <button class="copy" type="button" style=${{ marginLeft: ".4rem" }} onClick=${() => onOpenRun(id)}>open</button>
              <span class="mono" style=${{ marginLeft: ".4rem" }}>${id}</span></li>`)}</ul>` : null}
          <details><summary>Description (used as the intent)</summary>
            ${pr.body ? html`<pre>${pr.body}</pre>` : html`<p class="muted">The pull request has no description, so the intent is its title only.</p>`}</details>
          <details><summary>Run it yourself</summary><pre>${command}</pre><${Copy} text=${command} label="commands" /></details>
        </aside>` : html`<aside class="panel detail empty"><strong>No pull requests</strong>The repository has none yet.</aside>`}
      </div>
      ${asking && pr ? html`<${ConfirmVerify} pr=${pr} onConfirm=${start} onCancel=${() => setAsking(false)} />` : null}`;
  }

  function App() {
    const [route, setRoute] = useState(readHash);
    const [index, setIndex] = useState(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [theme, setTheme] = useState(() => store.get("pm-theme", "auto"));
    const [session, setSession] = useState({ token: "", prs: false });
    useEffect(() => { getJSON("/api/session").then(setSession).catch(() => {}); }, []);
    useEffect(() => {
      if (theme === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", theme);
      store.set("pm-theme", theme);
    }, [theme]);
    const load = useCallback(() => {
      setLoading(true);
      getJSON("/api/index").then((data) => { setIndex(data); setError(""); })
        .catch((e) => setError(e.message)).finally(() => setLoading(false));
    }, []);
    useEffect(load, [load]);
    const [fresh, setFresh] = useState(0);
    useEffect(() => {
      if (!index) return;
      const known = new Set(index.runs.map((r) => r.id));
      const timer = setInterval(() => {
        if (document.hidden) return;
        getJSON("/api/ids").then((ids) => setFresh(ids.filter((id) => !known.has(id)).length)).catch(() => {});
      }, 15000);
      setFresh(0);
      return () => clearInterval(timer);
    }, [index]);
    useEffect(() => {
      const onHash = () => setRoute(readHash());
      addEventListener("hashchange", onHash);
      return () => removeEventListener("hashchange", onHash);
    }, []);
    const go = (next) => { const state = { ...route, ...next }; setRoute(state); writeHash(state); };
    useEffect(() => {
      if (index && !route.run && index.runs.length) go({ run: [...index.runs].sort(SORTS.verdict)[0].id });
    }, [index]);
    const nextTheme = { auto: "light", light: "dark", dark: "auto" }[theme];
    return html`
      <header class="top">
        <div class="brand">ProofMerge<span>${index ? index.root : ""}</span></div>
        <nav class="tabs" role="tablist">
          ${[["runs", "Runs"], ["prs", "Pull requests"], ["bench", "Benchmarks"]].map(([key, label]) => html`
            <button key=${key} class="tab" role="tab" type="button" aria-selected=${route.tab === key}
              onClick=${() => go({ tab: key })}>${label}</button>`)}
        </nav>
        <span class="spacer"></span>
        <button class="ghost" type="button" onClick=${load} disabled=${loading}>${loading ? "Scanning…" : "Refresh"}</button>
        <button class="ghost" type="button" onClick=${() => setTheme(nextTheme)} aria-label=${"Theme: " + theme + ". Switch to " + nextTheme}>
          Theme: ${theme}</button>
      </header>
      <main>
        ${fresh ? html`<div class="banner" role="status">${fresh} new run${fresh === 1 ? "" : "s"} in this folder.
          <button class="primary" type="button" onClick=${load}>Show them</button></div>` : null}
        ${error ? html`<div class="error-box">Could not read runs: ${error}. Is <code>proofmerge dashboard</code> still running?</div>` : null}
        ${!index && !error ? html`<p class="boot">Scanning runs…</p>` : null}
        ${index && route.tab === "runs" ? html`<${RunsView} index=${index} selected=${route.run} onSelect=${(run) => go({ run })} />` : null}
        ${index && route.tab === "bench" ? html`<${BenchmarksView} index=${index} onOpenRun=${(run) => go({ tab: "runs", run })} />` : null}
        ${index && route.tab === "prs" ? html`<${PullsView} index=${index} token=${session.token} selected=${route.pr}
          onSelect=${(pr) => go({ pr: String(pr) })} onOpenRun=${(run) => { load(); go({ tab: "runs", run }); }}
          onRunsChanged=${load} />` : null}
      </main>`;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
})();
