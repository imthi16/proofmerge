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

  // Who writes the tests and who checks them. Meta's Muse models unless the run or the
  // chosen provider says otherwise; same rule as report.py's model_names.
  function runModels(usage) {
    if ((usage?.endpoint || "").startsWith("cli:")) return ["Claude", "Claude"];
    const name = (role, muse) => { const m = usage?.models?.[role] || ""; return !m || m.startsWith("muse-") ? muse : m; };
    return [name("spark", "Muse Spark"), name("wonder", "Muse Wonder")];
  }
  function chosenModels(model) {
    if (model?.provider === "claude") return ["Claude", "Claude"];
    if (model?.provider === "openai") return [model.spark_model || "Your model", model.wonder_model || "Your model"];
    return ["Muse Spark", "Muse Wonder"];
  }

  const store = {
    get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private window */ } },
  };

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    return { tab: params.get("tab") || "home", run: params.get("run") || "", pr: params.get("pr") || "" };
  }
  function writeHash(state) {
    const params = new URLSearchParams();
    if (state.tab !== "home") params.set("tab", state.tab);
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

  // The session token guards everything that starts work or shows local folders.
  let TOKEN = "";
  const authGet = (url) => fetch(url, { cache: "no-store", headers: { "X-ProofMerge-Token": TOKEN } })
    .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`); return d; });
  const post = (url, body) => fetch(url, { method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-ProofMerge-Token": TOKEN } })
    .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`); return d; });

  // One stroke icon set, drawn here so every icon shares weight and caps.
  const ICONS = {
    home: ["M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"],
    shield: ["M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z", "M9 12l2 2 4-4"],
    list: ["M9 6h11M9 12h11M9 18h11", "M4 6h.01M4 12h.01M4 18h.01"],
    pull: ["M6 7a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM6 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 21a2 2 0 1 0 0-4 2 2 0 0 0 0 4z", "M6 7v10M18 17V9a3 3 0 0 0-3-3h-4", "M13 4l-2 2 2 2"],
    chart: ["M4 20V11M10 20V5M16 20v-6M21 20H3"],
    sliders: ["M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1", "M15 4v4M9 10v4M17 16v4"],
    folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
    branch: ["M6 3v12", "M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M18 9a9 9 0 0 1-9 9"],
    play: ["M7 4.5v15l12-7.5z"],
    stop: ["M7 7h10v10H7z"],
    check: ["M5 12.5l4.5 4.5L19 7.5"],
    x: ["M6 6l12 12M18 6L6 18"],
    alert: ["M12 4 2.5 20h19z", "M12 10v4M12 17h.01"],
    power: ["M12 3v8", "M6.3 6.8a8 8 0 1 0 11.4 0"],
    up: ["M12 19V5M6 11l6-6 6 6"],
    file: ["M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z", "M14 3v5h5"],
    download: ["M12 4v11M7 10l5 5 5-5M5 20h14"],
    refresh: ["M20 12a8 8 0 1 1-2.3-5.7", "M20 4v5h-5"],
    spark: ["M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z"],
    box: ["M12 3l8 4.5v9L12 21l-8-4.5v-9z", "M4 7.5l8 4.5 8-4.5M12 12v9"],
    scale: ["M12 4v16M7 20h10M5 7h14", "M5 7l-2.5 6a2.5 2.5 0 0 0 5 0zM19 7l-2.5 6a2.5 2.5 0 0 0 5 0z"],
    eye: ["M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z", "M12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"],
    key: ["M14.5 13a4.5 4.5 0 1 0-4.2-2.9L3 17.4V21h3.6v-2.4H9v-2.4h2.4l1.7-1.7a4.5 4.5 0 0 0 1.4.5z", "M16 8h.01"],
    sun: ["M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"],
    git: ["M12 3l9 9-9 9-9-9z", "M12 8v8M12 8l3 3"],
    terminal: ["M4 5h16v14H4z", "M8 10l2.5 2L8 14M13 14h3"],
  };
  function Icon({ name, size = 18 }) {
    return html`<svg class="icon" width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      ${(ICONS[name] || []).map((d, i) => html`<path key=${i} d=${d} />`)}</svg>`;
  }
  const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const lastLine = (text) => (text || "").trim().split("\n").filter(Boolean).pop() || "";

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
          <button key=${d} type="button" class=${"seg " + d.toLowerCase()}
            aria-pressed=${filter === d} onClick=${() => setFilter(filter === d ? "" : d)}
            title=${filter === d ? "Show every verdict" : `Show only ${d}`}>
            <span class="k">${d}</span><span class="n">${counts[d]}</span>
          </button>`)}
        <span class="dist-note muted">${total} run${total === 1 ? "" : "s"}${unreadable ? `, ${unreadable} unreadable` : ""}${filter ? html`, showing ${filter} only <button class="copy" type="button"
          onClick=${() => setFilter("")}>clear</button>` : ""}</span>
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
          <th class="hide-sm col-shas">Base → head</th>
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
              <td class="hide-sm mono col-shas">${short(run.base_sha)} → ${short(run.head_sha)}</td>
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
      ${["base", "head"].map((revision, row) => {
        const runs = experiment.executions.filter((e) => e.revision === revision);
        return html`<${React.Fragment} key=${revision}>
          <span class="rev">${revision} <code>${short(runs[0]?.commit_sha)}</code></span>
          ${attempts.map((a, col) => {
            const outcome = runs.find((e) => e.attempt === a)?.outcome || "—";
            const key = `${revision}:${a}`;
            return html`<button key=${a} type="button" class=${"cell " + outcome} aria-pressed=${open === key}
              style=${{ "--i": row * attempts.length + col }}
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

  function Hypothesis({ h, proof, probe, failure, logs, fix }) {
    const [open, setOpen] = useState("");
    const experiment = proof.experiments.find((e) => e.hypothesis_id === h.id);
    const finding = proof.findings.find((f) => f.hypothesis_id === h.id);
    const claim = proof.intent.claims.find((c) => c.id === h.claim_id);
    const checker = runModels(proof.model_usage)[1];
    return html`<article class="hyp">
      <h3><code>${h.id}</code> ${h.title}</h3>
      <p class="meta">cites <code>${h.claim_id}</code> ${claim ? claim.kind : "unknown claim"}, risk: ${h.risk_category}${h.possibly_intended ? ", possibly intended" : ""}</p>
      ${claim?.source_quote ? html`<blockquote>${claim.source_quote}</blockquote>` : null}
      ${proof.mismatched_probe_ids.includes(h.id) ? html`<p class="note">${checker}: probe may not test this hypothesis</p>` : null}
      ${proof.explained_probe_ids.includes(h.id) ? html`<p class="note">${checker}: may be the intended change ${proof.explained_probe_claim_ids[h.id] || ""}</p>` : null}
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
      ${fix ? html`<${FixView} fix=${fix} />` : null}
    </article>`;
  }

  const AppCtx = React.createContext(null);

  // The detail scrolls with the page, not in a box of its own. After a pick, bring its top into
  // view when it is out of sight: below the list on one column, or scrolled past on two.
  function revealDetail() {
    requestAnimationFrame(() => {
      const detail = document.querySelector(".detail");
      if (!detail) return;
      const top = detail.getBoundingClientRect().top;
      if (top < 0 || top > innerHeight * .8) detail.scrollIntoView({ block: "start", behavior: "smooth" });
    });
  }

  function Detail({ id }) {
    const ctx = React.useContext(AppCtx);
    const [state, setState] = useState({ loading: true });
    const [posting, setPosting] = useState(false);
    const [approving, setApproving] = useState(false);
    const fixDone = ctx.job?.command === "fix" && ctx.job.status !== "running" ? ctx.job.finished : 0;
    useEffect(() => {
      if (!id) return;
      let live = true;
      setState({ loading: true });
      getJSON("/api/run?id=" + encodeURIComponent(id))
        .then((data) => live && setState({ data }))
        .catch((error) => live && setState({ error: error.message }));
      return () => { live = false; };
    }, [id, fixDone]);
    if (!id) return html`<aside class="panel detail empty"><strong>Pick a run</strong>Its verdict, probes and evidence show here.</aside>`;
    if (state.error) return html`<aside class="panel detail"><div class="error-box">Could not load this run: ${state.error}</div></aside>`;
    if (state.loading) return html`<aside class="panel detail muted">Loading ${id}…</aside>`;
    const { summary, proof, probes, failures, logs, fix } = state.data;
    // Runs started from the Pull requests page are named pr-<number>-<sha>-<time>.
    const prNumber = Number((/^pr-(\d+)-[0-9a-f]{7}-\d+$/.exec(id) || [])[1]) || 0;
    const decision = proof.verdict.decision;
    const usage = proof.model_usage;
    const [writer, checker] = runModels(usage);
    const models = writer === checker ? `${writer} wrote and checked the tests` : `${writer} wrote the tests, ${checker} checked them`;
    // The id is relative to the dashboard folder, so the command uses the absolute path.
    const replay = `proofmerge replay '${summary.proof_path.replaceAll("'", "'\\''")}'`;
    return html`<${React.Fragment}><aside class="panel detail" aria-label=${"Run " + id}>
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
        <dt>models</dt><dd>${models}${usage.endpoint ? html` via <code>${usage.endpoint}</code>` : null}</dd>
        ${summary.benchmark ? html`<dt>label</dt><dd>${summary.benchmark.label}${summary.benchmark.held_out ? ", held out" : ""}
          ${disagrees(summary) ? html`<span class="disagree">disagrees with the verdict</span>` : null}</dd>` : null}
      </dl>
      ${summary.integrity.status === "changed" ? html`<ul class="reasons">${summary.integrity.problems.map((p, i) => html`<li key=${i} class="disagree">${p}</li>`)}</ul>` : null}
      <div class="actions">
        ${summary.has_report ? html`<a class="primary" href=${"/report?id=" + encodeURIComponent(id)} target="_blank" rel="noopener"><${Icon} name="file" />Open full report</a>
          <a class="ghost" href=${"/report?id=" + encodeURIComponent(id)} download=${(proof.run_id || "proofmerge") + "-report.html"}><${Icon} name="download" />Download</a>` : null}
        <button class="ghost" type="button" disabled=${ctx.busy} onClick=${() => ctx.start({ command: "replay", run: id })}
          title="Checks every file of this run against its sealed hashes"><${Icon} name="shield" />Check evidence</button>
        <button class="ghost" type="button" disabled=${ctx.busy || !ctx.settings.project}
          onClick=${() => ctx.start({ command: "rerun", run: id, project: ctx.settings.project })}
          title=${ctx.settings.project ? "Runs the recorded probes again in the sandbox" : "Choose the project on Home first"}><${Icon} name="refresh" />Run the tests again</button>
      </div>
      ${decision === "BLOCK" ? html`<div class="fix-actions">
        <button class="primary" type="button" disabled=${ctx.busy || !ctx.settings.project || summary.integrity.status === "changed"}
          onClick=${() => ctx.start({ command: "fix", run: id, project: ctx.settings.project })}
          title=${ctx.settings.project ? "The model writes a fix for each verified regression; ProofMerge re-runs the failing probe on it in the sandbox" : "Choose the project on Home first"}>
          <${Icon} name="spark" />${fix ? "Suggest the fix again" : "Suggest a fix"}</button>
        ${prNumber ? html`<button class="ghost" type="button" disabled=${ctx.busy || !ctx.settings.project} onClick=${() => setPosting(true)}
          title="Posts the verdict and the suggested fix to the pull request, after you confirm"><${Icon} name="pull" />Post to PR #${prNumber}</button>` : null}
        ${fix ? html`<span class="muted fix-note">${fix.fixes.filter((f) => f.verified).length} of ${fix.fixes.length} fix${fix.fixes.length === 1 ? "" : "es"} verified</span>` : null}
      </div>` : null}
      ${decision === "PASS" && prNumber ? html`<div class="fix-actions">
        <button class="primary" type="button" disabled=${ctx.busy || !ctx.settings.project || summary.integrity.status === "changed"}
          onClick=${() => setApproving(true)}
          title=${ctx.settings.project ? "Approves the pull request on GitHub as you, after you confirm" : "Choose the project on Home first"}>
          <${Icon} name="shield" />Approve PR #${prNumber}</button>
        <span class="muted fix-note">PASS means no regression was found in the tested scope, not that the change is correct.</span>
      </div>` : null}
      <details class="cli"><summary><${Icon} name="terminal" size=${15} />Terminal command</summary>
        <pre>${replay}</pre><${Copy} text=${replay} label="replay command" /></details>
    </aside>
    <section class="panel evidence" aria-label=${"Evidence for run " + id}>
      <h2>Hypotheses and probes</h2>
      ${proof.hypotheses.hypotheses.length ? proof.hypotheses.hypotheses.map((h) => html`
        <${Hypothesis} key=${h.id} h=${h} proof=${proof} probe=${probes[h.id]} failure=${failures[h.id]} logs=${logs || {}}
          fix=${fix?.fixes.find((f) => f.hypothesis_id === h.id)} />`)
        : html`<p class="muted">No hypotheses were recorded for this run.</p>`}
      <h2>Test-tamper guard</h2>
      ${proof.tamper_findings.length ? html`<ul class="reasons">${proof.tamper_findings.map((t, i) => html`<li key=${i}>
        <code>${t.test_path}</code>${t.test_name ? html` :: <code>${t.test_name}</code>` : null} — ${t.kind}${t.base_test_on_head_outcome ? `, base test on head: ${t.base_test_on_head_outcome}` : ""}</li>`)}</ul>`
        : html`<p class="muted">No tamper finding was recorded.</p>`}
      <h2>Intent claims</h2>
      ${proof.intent.claims.map((c) => html`<div key=${c.id} style=${{ marginBottom: ".6rem" }}>
        <code>${c.id}</code> <span class=${"kind " + c.kind}>${c.kind}</span>
        ${proof.contested_claim_ids.includes(c.id) ? html` <span class="kind">contested by ${checker}</span>` : null}
        <div>${c.statement}</div>
        ${c.source_quote ? html`<blockquote>${c.source_quote}</blockquote>` : html`<div class="muted">implicit claim — not stated in the intent text</div>`}
      </div>`)}
    </section>
    ${approving ? html`<${ConfirmApprove} pr=${prNumber} onCancel=${() => setApproving(false)}
      onConfirm=${() => { setApproving(false); ctx.start({ command: "approve", run: id, project: ctx.settings.project, pr: prNumber }); }} />` : null}
    ${posting ? html`<${ConfirmPost} pr=${prNumber} fix=${fix} onCancel=${() => setPosting(false)}
      onConfirm=${(mode) => { setPosting(false); ctx.start({ command: "post", run: id, project: ctx.settings.project, pr: prNumber, mode }); }} />` : null}<//>`;
  }

  // One suggested fix, with how it was checked. A fix never changes the verdict.
  function FixView({ fix }) {
    return html`<section class=${"fix " + (fix.verified ? "verified" : "unverified")} aria-label=${"Suggested fix for " + fix.hypothesis_id}>
      <h4><${Icon} name=${fix.verified ? "check" : "alert"} size=${15} />${fix.verified ? "Fix verified" : "Suggested fix, not verified"}</h4>
      <p class="fix-proof">${fix.verified
        ? `The probe passes on base (${fix.base_outcomes}) and on the fixed head (${fix.fixed_outcomes}). Only this probe was re-run.`
        : fix.problem}</p>
      ${fix.explanation ? html`<p>${fix.explanation}</p>` : null}
      ${fix.patch ? html`<pre class="diff">${fix.patch.split("\n").map((line, i) => html`<span key=${i}
        class=${line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : ""}>${line}\n</span>`)}</pre>
        <${Copy} text=${fix.patch} label="the fix as a patch" />` : null}
    </section>`;
  }

  function ConfirmPonyPost({ pr, count, account, accounts, onSetup, onConfirm, onCancel }) {
    const cancel = useRef(null);
    useEffect(() => {
      cancel.current?.focus();
      const onKey = (event) => { if (event.key === "Escape") onCancel(); };
      addEventListener("keydown", onKey);
      return () => removeEventListener("keydown", onKey);
    }, []);
    return html`<div class="scrim" onClick=${(e) => e.target === e.currentTarget && onCancel()}>
      <div class="dialog panel" role="dialog" aria-modal="true" aria-labelledby="ponypost-title">
        <h3 id="ponypost-title">Post ${count} finding${count === 1 ? "" : "s"} to PR #${pr.number}?</h3>
        <${ActingAs} account=${account} accounts=${accounts} onSetup=${onSetup} />
        <p>ProofMerge posts one review on the pull request. Each finding becomes a comment on its lines when GitHub's diff has them; the others go in the review summary.</p>
        <p class="note">It is a comment only: it never approves or requests changes. If the pull request has new commits since the review, nothing is posted.</p>
        <div class="actions">
          <button class="primary" type="button" onClick=${onConfirm}>Post to PR #${pr.number}</button>
          <button ref=${cancel} class="ghost" type="button" onClick=${onCancel}>Cancel</button>
        </div>
      </div>
    </div>`;
  }

  function ConfirmPost({ pr, fix, onConfirm, onCancel }) {
    const [mode, setMode] = useState("inline");
    const cancel = useRef(null);
    useEffect(() => {
      cancel.current?.focus();
      const onKey = (event) => { if (event.key === "Escape") onCancel(); };
      addEventListener("keydown", onKey);
      return () => removeEventListener("keydown", onKey);
    }, []);
    const options = [["inline", "On the changed lines", "Each fix becomes a GitHub suggestion on the lines it changes, which the author can commit from the page. Anything outside the pull request's diff goes in the summary."],
      ["overall", "One overall comment", "The verdict, the reasons and each fix as a diff, in one comment."]];
    return html`<div class="scrim" onClick=${(e) => e.target === e.currentTarget && onCancel()}>
      <div class="dialog panel" role="dialog" aria-modal="true" aria-labelledby="post-title">
        <h3 id="post-title">Post to PR #${pr}?</h3>
        <p>ProofMerge posts one review comment as your GitHub account: the verdict, its reasons${fix ? " and the suggested fixes" : ""}.
          It never approves or requests changes.</p>
        ${fix ? null : html`<p class="note">No fix has been suggested for this run, so only the verdict is posted.</p>`}
        <div class="post-modes" role="radiogroup" aria-label="Where to post">${options.map(([value, label, note]) => html`
          <label key=${value} class=${"post-mode" + (mode === value ? " on" : "")}>
            <input type="radio" name="post-mode" value=${value} checked=${mode === value} onChange=${() => setMode(value)} />
            <span><strong>${label}</strong><span class="muted">${note}</span></span></label>`)}</div>
        <div class="actions">
          <button class="primary" type="button" onClick=${() => onConfirm(mode)}>Post to PR #${pr}</button>
          <button ref=${cancel} class="ghost" type="button" onClick=${onCancel}>Cancel</button>
        </div>
      </div>
    </div>`;
  }

  function ConfirmApprove({ pr, onConfirm, onCancel }) {
    const cancel = useRef(null);
    useEffect(() => {
      cancel.current?.focus();
      const onKey = (event) => { if (event.key === "Escape") onCancel(); };
      addEventListener("keydown", onKey);
      return () => removeEventListener("keydown", onKey);
    }, []);
    return html`<div class="scrim" onClick=${(e) => e.target === e.currentTarget && onCancel()}>
      <div class="dialog panel" role="dialog" aria-modal="true" aria-labelledby="approve-title">
        <h3 id="approve-title">Approve PR #${pr}?</h3>
        <p>ProofMerge approves the pull request as your GitHub account, on the commit it checked. If the pull request
          has changed since this run, nothing is posted.</p>
        <p class="note">PASS means no regression was found within the tested scope. It does not prove the change correct.</p>
        <div class="actions">
          <button class="primary" type="button" onClick=${onConfirm}>Approve PR #${pr}</button>
          <button ref=${cancel} class="ghost" type="button" onClick=${onCancel}>Cancel</button>
        </div>
      </div>
    </div>`;
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
    const pick = (id) => { onSelect(id); revealDetail(); };
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
      return html`<div class="panel empty"><strong>No checks yet</strong>
        Check a change on Home and its result shows up here, with all its evidence.</div>`;
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
      <div class="split runs-split">
        ${runs.length ? html`<${RunsTable} runs=${runs} selected=${selected} onSelect=${pick} sort=${sort} setSort=${setSort} />`
          : html`<div class="panel empty"><strong>No run matches</strong>Change the search or filters.</div>`}
        <${Detail} key=${selected} id=${selected} />
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
    const pony = data?.ponytail_job;
    const postJob = data?.ponytail_post_job;
    const [ponyGone, setPonyGone] = useState(0);
    const [postGone, setPostGone] = useState(0);
    const [postAsk, setPostAsk] = useState(false);
    const { gh } = useGitHub();  // read with the other hooks: who the post would be made as
    const goSetup = () => { location.hash = "tab=setup"; };
    useEffect(() => {
      if (lastStatus.current === "running" && job && job.status !== "running") onRunsChanged();
      lastStatus.current = job?.status;
      if (job?.status !== "running" && pony?.status !== "running" && postJob?.status !== "running") return;
      const timer = setTimeout(() => load(false), 2000);
      return () => clearTimeout(timer);
    }, [data, tick]);
    if (error && !data) {
      return html`<div class="panel empty"><strong>Pull requests are not available</strong>${error}
        <div class="muted" style=${{ marginTop: ".5rem" }}>Pull requests come from GitHub through the GitHub CLI (gh), signed in. Setup shows whether it is installed.</div></div>`;
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
    const ponyRunning = pony?.status === "running";
    const ponyOnThis = ponyRunning && pr && pony.number === pr.number;
    const postOnThis = postJob?.status === "running" && pr && postJob.number === pr.number;
    const startPost = () => {
      setPostAsk(false); setPosting("");
      fetch("/api/ponytail/post", { method: "POST", headers: { "Content-Type": "application/json", "X-ProofMerge-Token": token },
        body: JSON.stringify({ number: pr.number }) })
        .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`); })
        .then(() => load(false)).catch((e) => setPosting(e.message));
    };
    const startPony = () => {
      setPosting("");
      fetch("/api/ponytail", { method: "POST", headers: { "Content-Type": "application/json", "X-ProofMerge-Token": token },
        body: JSON.stringify({ number: pr.number }) })
        .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`); })
        .then(() => load(false)).catch((e) => setPosting(e.message));
    };
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
          ${prs.length ? html`<table class="pr-table">
            <thead><tr><th>PR</th><th>Title</th><th class="hide-sm">Branch</th><th>ProofMerge</th></tr></thead>
            <tbody>${prs.map((p) => {
              const verdict = p.runs.length ? verdictOf(p.runs[p.runs.length - 1]) : null;
              return html`<tr key=${p.number} aria-selected=${pr && p.number === pr.number} onClick=${() => { onSelect(p.number); revealDetail(); }}>
                <td class="mono">#${p.number}</td>
                <td><div class="run-name">${p.title}</div>
                  <div class="run-sub">${p.draft ? "Draft, " : ""}${STATE[p.state] || p.state}, by ${p.author}</div></td>
                <td class="hide-sm mono" style=${{ fontSize: ".8rem" }}>${p.head} → ${p.base}</td>
                <td>${verdict ? html`<span class=${"pill " + verdict}>${verdict}</span>`
                  : running && job.number === p.number ? html`<span class="pill">RUNNING</span>`
                  : html`<span class="muted" style=${{ fontSize: ".85rem" }}>not verified</span>`}
                  <${PonyChip} pr=${p} pony=${pony} /></td>
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
            <button class="ghost" type="button" disabled=${ponyRunning || pr.state !== "OPEN"} onClick=${startPony}
              title="One model call reads the diff and lists what could be deleted or shortened. No sandbox, and no verdict.">
              <${Icon} name="spark" />${ponyOnThis ? "Reviewing…" : pr.ponytail ? "Review again" : "Ponytail review"}</button>
            <a class="ghost" href=${pr.url} target="_blank" rel="noopener noreferrer">Open on GitHub</a>
          </div>
          ${ponyOnThis ? html`<${PonyProgress} job=${pony} />` : null}
          ${pony?.status === "failed" && pony.number === pr.number && pony.error && ponyGone !== pony.created
            ? html`<${Alert} message=${"The Ponytail review did not finish. " + pony.error} seconds=${60} onClose=${() => setPonyGone(pony.created)} style=${{ marginTop: ".75rem" }} />` : null}
          ${postJob?.status === "failed" && postJob.number === pr.number && postJob.error && postGone !== postJob.created
            ? html`<${Alert} message=${"Nothing was posted. " + postJob.error} seconds=${60} onClose=${() => setPostGone(postJob.created)} style=${{ marginTop: ".75rem" }} />` : null}
          ${pr.ponytail && !ponyOnThis ? html`<${PonytailBlock} ponytail=${pr.ponytail} pr=${pr} posting=${postOnThis}
            account=${gh?.user || ""} onPost=${() => setPostAsk(true)} />` : null}
          ${pr.state !== "OPEN" ? html`<p class="muted" style=${{ fontSize: ".88rem" }}>This pull request is ${(STATE[pr.state] || pr.state).toLowerCase()}, so its base already contains it. Only open pull requests can be verified.</p>` : null}
          ${posting ? html`<${Alert} message=${posting} onClose=${() => setPosting("")} style=${{ marginTop: ".75rem" }} />` : null}
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
          <h2>Description <span class="muted h-note">used as the intent</span></h2>
          ${pr.body ? html`<div class="md">${window.ProofMergeMarkdown(React.createElement, pr.body)}</div>`
            : html`<p class="muted">The pull request has no description, so the intent is its title only.</p>`}
          <details><summary>Run it yourself</summary><pre>${command}</pre><${Copy} text=${command} label="commands" /></details>
        </aside>` : html`<aside class="panel detail empty"><strong>No pull requests</strong>The repository has none yet.</aside>`}
      </div>
      ${asking && pr ? html`<${ConfirmVerify} pr=${pr} onConfirm=${start} onCancel=${() => setAsking(false)} />` : null}
      ${postAsk && pr?.ponytail ? html`<${ConfirmPonyPost} pr=${pr} count=${pr.ponytail.findings.length} account=${gh?.user || ""}
        accounts=${gh?.accounts?.length || 0} onSetup=${goSetup} onConfirm=${startPost} onCancel=${() => setPostAsk(false)} />` : null}`;
  }

  // ---------------------------------------------------------------- the app shell
  const VERDICT_JOBS = ["verify", "probe", "hypotheses"];
  const JOB_TITLE = {
    verify: "Checking the change", probe: "Writing and running tests", hypotheses: "Listing what could break",
    replay: "Checking the evidence", rerun: "Running the tests again", smoke: "Testing the model connection",
    setup: "Building the sandbox image",
    fix: "Suggesting and checking a fix", post: "Posting to the pull request",
  };
  const MODES = [
    ["verify", "Full check", "Writes tests that try to break the change, runs them in the sandbox and gives PASS, REVIEW or BLOCK."],
    ["hypotheses", "Only list risks", "Lists what could break and why, without running anything. Two model calls."],
    ["probe", "Tests, no verdict", "Writes the tests and runs them on both branches, but stops before the verdict."],
  ];
  const stationsFor = ([writer, checker]) => [
    ["eye", "Read the change", "The diff and your description"],
    ["spark", `${writer} looks for risks`, "Each risk becomes a test"],
    ["box", "Tests run in the sandbox", "On both branches, three times"],
    ["scale", `${checker} checks the evidence`, "Does each test prove its claim"],
    ["shield", "Verdict", "PASS, REVIEW or BLOCK"],
  ];

  function FolderPicker({ start, onPick, onClose }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [typed, setTyped] = useState("");
    const open = (path) => authGet("/api/browse?path=" + encodeURIComponent(path || ""))
      .then((d) => { setData(d); setTyped(d.path); setError(""); }).catch((e) => setError(e.message));
    useEffect(() => { open(start); }, []);
    useEffect(() => {
      const onKey = (event) => { if (event.key === "Escape") onClose(); };
      addEventListener("keydown", onKey);
      return () => removeEventListener("keydown", onKey);
    }, []);
    return html`<div class="scrim" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="dialog picker panel" role="dialog" aria-modal="true" aria-labelledby="picker-title">
        <header class="picker-head">
          <h3 id="picker-title">Choose your project folder</h3>
          <button class="icon-btn" type="button" onClick=${onClose} aria-label="Close"><${Icon} name="x" /></button>
        </header>
        <p class="muted">Pick the folder that holds your code. Folders marked Git project can be checked.</p>
        <div class="places">${(data?.places || []).map((p) => html`<button key=${p.path} type="button" class="chip"
          onClick=${() => open(p.path)}>${p.name}</button>`)}</div>
        <form class="path-bar" onSubmit=${(e) => { e.preventDefault(); open(typed); }}>
          <button class="icon-btn" type="button" disabled=${!data?.parent} onClick=${() => open(data.parent)} aria-label="Up one folder"><${Icon} name="up" /></button>
          <input class="search" value=${typed} onInput=${(e) => setTyped(e.target.value)} aria-label="Folder path" spellcheck="false" />
          <button class="ghost" type="submit">Go</button>
        </form>
        ${error ? html`<${Alert} message=${error} onClose=${() => setError("")} />` : null}
        <ul class="dirs" role="list">
          ${data && !data.dirs.length ? html`<li class="muted dir-empty">No folders inside this one.</li>` : null}
          ${(data?.dirs || []).map((d) => html`<li key=${d.path}>
            <button type="button" class="dir" onClick=${() => open(d.path)}>
              <${Icon} name=${d.git ? "git" : "folder"} /><span>${d.name}</span>
              ${d.git ? html`<span class="tag">Git project</span>` : null}</button>
            ${d.git ? html`<button type="button" class="ghost small" onClick=${() => onPick(d.path)}>Use</button>` : null}
          </li>`)}
        </ul>
        <footer class="picker-foot">
          <span class="muted mono">${data?.path || ""}</span>
          <button class="primary" type="button" disabled=${!data?.git} onClick=${() => onPick(data.path)}
            title=${data?.git ? "" : "This folder is not a Git project"}>Use this folder</button>
        </footer>
      </div>
    </div>`;
  }

  function Composer() {
    const ctx = React.useContext(AppCtx);
    const { settings, health } = ctx;
    const [picking, setPicking] = useState(false);
    const [info, setInfo] = useState(null);
    const [error, setError] = useState("");
    const [base, setBase] = useState("");
    const [head, setHead] = useState("");
    const [intent, setIntent] = useState(() => store.get("pm-intent", ""));
    const [mode, setMode] = useState("verify");
    useEffect(() => { store.set("pm-intent", intent); }, [intent]);
    useEffect(() => {
      if (!settings.project) return;
      authGet("/api/branches?project=" + encodeURIComponent(settings.project))
        .then((d) => { setInfo(d); setBase(d.base); setHead(d.head); setError(""); })
        .catch((e) => { setInfo(null); setError(e.message); });
    }, [settings.project]);
    const pick = (path) => { setPicking(false); ctx.saveSettings({ project: path }).catch((e) => setError(e.message)); };
    const needsSandbox = mode !== "hypotheses";
    const blocker = !settings.project ? "Choose your project folder first."
      : !info ? "" : info.branches.length < 2 ? "This project needs at least two branches."
      : !head || !base ? "Pick both branches." : head === base ? "Pick two different branches."
      : health && !health.model_key ? "Choose your AI model in Setup."
      : health && needsSandbox && !health.docker_running ? "Start Docker Desktop, then check again in Setup."
      : ctx.busy ? "Another action is running." : "";
    const fixable = /Setup/.test(blocker);
    const submit = (event) => {
      event.preventDefault();
      if (!blocker) ctx.start({ command: mode, project: settings.project, base, head, intent });
    };
    const options = (info?.branches || []).map((b) => html`<option key=${b.name} value=${b.name}>${b.name}</option>`);
    const headInfo = info?.branches.find((b) => b.name === head);
    return html`<form class="composer panel" onSubmit=${submit}>
      <button type="button" class="project" onClick=${() => setPicking(true)}>
        <span class="project-icon"><${Icon} name="folder" size=${20} /></span>
        <span class="project-text">
          <strong>${info?.name || (settings.project ? settings.project.split(/[\\/]/).pop() : "Choose your project folder")}</strong>
          <span class="muted mono">${settings.project || "The folder that holds your code"}</span>
        </span>
        <span class="chip">${settings.project ? "Change" : "Browse"}</span>
      </button>
      ${error ? html`<${Alert} message=${error} onClose=${() => setError("")} />` : null}
      <div class="sentence">
        <span>Check</span>
        <label class="select-wrap"><${Icon} name="branch" size=${16} />
          <select value=${head} onChange=${(e) => setHead(e.target.value)} aria-label="Branch with the change" disabled=${!info}>
            ${options}</select></label>
        <span>before it goes into</span>
        <label class="select-wrap"><${Icon} name="branch" size=${16} />
          <select value=${base} onChange=${(e) => setBase(e.target.value)} aria-label="Branch it merges into" disabled=${!info}>
            ${options}</select></label>
      </div>
      ${headInfo ? html`<p class="hint muted">Latest on ${head}: ${headInfo.subject || "no message"}, ${headInfo.when}.</p>` : null}
      <label class="intent">
        <span class="sr-only">What the change should do</span>
        <textarea rows="4" value=${intent} onInput=${(e) => setIntent(e.target.value)}
          placeholder="What should this change do? Paste the pull request description. ProofMerge checks the change against it, and without it the result is more cautious."></textarea>
      </label>
      <footer class="composer-foot">
        <div class="modes" role="radiogroup" aria-label="What to do">
          ${MODES.map(([key, label]) => html`<button key=${key} type="button" role="radio" aria-checked=${mode === key}
            onClick=${() => setMode(key)}>${label}</button>`)}
        </div>
        <p class="mode-note muted">${MODES.find((m) => m[0] === mode)[2]}</p>
        <div class="go">
          ${blocker ? html`<span class="blocker">${blocker}${fixable ? html` <button type="button" class="link" onClick=${() => ctx.go({ tab: "setup" })}>Open Setup</button>` : null}</span>` : null}
          <button class="primary big" type="submit" disabled=${!!blocker}><${Icon} name="play" />${MODES.find((m) => m[0] === mode)[1] === "Full check" ? "Check this change" : MODES.find((m) => m[0] === mode)[1]}</button>
        </div>
      </footer>
      ${picking ? html`<${FolderPicker} start=${settings.project} onPick=${pick} onClose=${() => setPicking(false)} />` : null}
    </form>`;
  }

  function Pipeline({ job }) {
    const ctx = React.useContext(AppCtx);
    const [now, setNow] = useState(Date.now() / 1000);
    const running = job.status === "running";
    useEffect(() => {
      if (!running) return;
      const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
      return () => clearInterval(timer);
    }, [running]);
    const elapsed = Math.max(0, (job.finished || now) - job.started);
    const failed = !running && job.status !== "done";
    const active = job.status === "done" ? 5 : [0, 1, 2, 2, 3, 5][job.stage] ?? 0;
    const all = stationsFor(chosenModels(ctx.settings.model));
    const stations = job.command === "hypotheses" ? all.slice(0, 2) : job.command === "probe" ? all.slice(0, 3) : all;
    const shown = Math.min(active, stations.length);
    // A finished verify ticks only the steps whose files exist; the rest did not run.
    const skipped = (i) => !running && !failed && job.command === "verify" && job.seen && !job.seen[i];
    const anySkipped = stations.some((_, i) => skipped(i));
    const title = running ? JOB_TITLE[job.command] : failed ? (job.status === "failed" ? "The check could not finish" : `The check was ${job.status}`) : `Finished in ${clock(elapsed)}`;
    return html`<section class=${"live panel" + (failed ? " is-failed" : "")} aria-live="polite">
      <header class="live-head">
        <div>
          <h2>${title}</h2>
          <p class="muted">${running ? "This can take a few minutes. You can leave this page; the check keeps going." : failed ? "Nothing was merged or changed in your project."
            : anySkipped ? "Some steps did not run. The verdict below says why." : "Every step ran."}</p>
        </div>
        <span class="timer mono" aria-label="Time taken">${clock(elapsed)}</span>
        ${running ? html`<button class="ghost" type="button" onClick=${ctx.stop}><${Icon} name="stop" />Stop</button>` : null}
      </header>
      <ol class=${"track n" + stations.length} style=${{ "--fill": anySkipped ? 0 : stations.length > 1 ? Math.min(shown, stations.length - 1) / (stations.length - 1) : 0 }}>
        ${stations.map(([icon, name, note], i) => {
          const state = skipped(i) ? "skipped" : i < shown ? "done" : i === shown && running ? "active" : i === shown && failed ? "failed" : "todo";
          return html`<li key=${name} class=${"station " + state} style=${{ "--i": i }}>
            <span class="node"><${Icon} name=${state === "done" ? "check" : state === "failed" ? "x" : icon} /></span>
            <span class="station-text"><strong>${name}</strong><span class="muted">${state === "skipped" ? "Did not run" : note}</span></span>
          </li>`;
        })}
      </ol>
      <p class="hint muted">Progress comes from the files this run has written so far.</p>
      <details class="log-live" open=${failed}><summary>${failed ? "What went wrong" : "Show what is happening"}</summary>
        <pre>${job.output || "Starting…"}</pre></details>
      ${failed ? html`<div class="actions">
        <button class="primary" type="button" onClick=${ctx.dismiss}>Try again</button>
        <button class="ghost" type="button" onClick=${() => ctx.go({ tab: "setup" })}>Check Setup</button></div>` : null}
    </section>`;
  }

  function VerdictReveal({ job }) {
    const ctx = React.useContext(AppCtx);
    const [data, setData] = useState(null);
    useEffect(() => { getJSON("/api/run?id=" + encodeURIComponent(job.run)).then(setData).catch(() => setData(false)); }, [job.run]);
    if (data === null) return null;
    if (data === false) return html`<p class="muted">The run finished, but its proof could not be read. Open Runs to look at it.</p>`;
    const decision = data.proof.verdict.decision;
    return html`<section class=${"reveal panel " + decision}>
      <p class=${"decision " + decision}>${decision}</p>
      <p class="reveal-head">${HEADLINE[decision]}</p>
      <ul class="reasons">${data.proof.verdict.reasons.map((r, i) => html`<li key=${i} style=${{ "--i": i }}>${r}</li>`)}</ul>
      <div class="actions">
        ${data.summary.has_report ? html`<a class="primary" href=${"/report?id=" + encodeURIComponent(job.run)} target="_blank" rel="noopener"><${Icon} name="file" />Open the report</a>` : null}
        <button class="ghost" type="button" onClick=${() => ctx.go({ tab: "runs", run: job.run })}><${Icon} name="list" />See every detail</button>
        <button class="ghost" type="button" onClick=${ctx.dismiss}><${Icon} name="play" />Check another change</button>
      </div>
    </section>`;
  }

  function Risks({ job }) {
    const ctx = React.useContext(AppCtx);
    const text = job.output || "";
    let found = null;
    try { found = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1)); } catch { found = null; }
    return html`<section class="reveal panel">
      ${found ? html`<h2>What could break</h2>
        ${found.hypotheses.hypotheses.length ? html`<ol class="risk-list">${found.hypotheses.hypotheses.map((h, i) => html`<li key=${h.id} style=${{ "--i": i }}>
          <h3><code>${h.id}</code> ${h.title}</h3>
          <p class="meta">risk: ${h.risk_category}, cites <code>${h.claim_id}</code></p>
          <p>${h.suspected_behavior}</p></li>`)}</ol>`
          : html`<p class="muted">${chosenModels(ctx.settings.model)[0]} found nothing it could test.</p>`}
        <h3 class="sub">What the change claims</h3>
        <ul class="reasons">${found.intent.claims.map((c) => html`<li key=${c.id}><code>${c.id}</code> <span class=${"kind " + c.kind}>${c.kind}</span> ${c.statement}</li>`)}</ul>`
        : html`<h2>Done</h2><pre>${text}</pre>`}
      <div class="actions"><button class="primary" type="button" onClick=${ctx.dismiss}>Check another change</button></div>
    </section>`;
  }

  // The Ponytail review of a pull request: what to delete or shorten. It reads the diff and changes no verdict.
  function PonytailBlock({ ponytail, pr, posting, account, onPost }) {
    const n = ponytail.findings.length;
    return html`<section class="ponytail" aria-label="Ponytail review">
      <div class="pony-head">
        <h4>Ponytail review</h4>
        <span class="muted">Over-engineering only. It reads the diff and runs nothing.</span>
      </div>
      <p class="pony-summary">${n
        ? html`<strong>${n} finding${n === 1 ? "" : "s"}</strong>${ponytail.net_lines ? `, about ${ponytail.net_lines} line${ponytail.net_lines === 1 ? "" : "s"} could go` : ""}.`
        : html`<strong>Lean already.</strong> There is nothing to cut in this diff.`}
        <span class="muted"> Reviewed at <code>${short(ponytail.head_sha)}</code>.</span></p>
      ${n ? html`<ol class="pony-list">${ponytail.findings.map((f, i) => html`<li key=${i} class="pony-item">
        <div class="pony-where"><span class="kind">${f.tag}</span><code>${f.location}</code></div>
        <p class="pony-what">${f.what}</p>
        <p class="pony-instead"><span class="muted">${/^nothing\.?$/i.test(f.replacement.trim()) ? "Nothing replaces it." : "Instead:"}</span>${/^nothing\.?$/i.test(f.replacement.trim()) ? "" : " " + f.replacement}</p>
      </li>`)}</ol>` : null}
      ${ponytail.truncated ? html`<p class="muted fix-note">The diff was long, so part of it was not reviewed.</p>` : null}
      ${n && pr ? html`<div class="pony-post">${ponytail.posted_url
        ? html`<span class="muted">Posted to the pull request.</span> <a href=${ponytail.posted_url} target="_blank" rel="noopener noreferrer">View the review on GitHub</a>`
        : html`<button class="primary btn-small" type="button" disabled=${posting || pr.state !== "OPEN"} onClick=${onPost}
            title="Posts these findings to the pull request as review comments, after you confirm.">
            ${posting ? "Posting…" : n === 1 ? "Post as a review comment" : `Post ${n} findings as review comments`}${account && !posting ? html`<span class="as-account">as @${account}</span>` : null}</button>
          <span class="muted">Each finding goes on its lines when they are in the diff.</span>`}</div>` : null}
    </section>`;
  }

  // While a review runs: the two real steps, and how long it has taken.
  function PonyProgress({ job }) {
    const [now, setNow] = useState(() => Date.now() / 1000);
    useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
    const reading = /^Reading/.test(job.step || "");
    const steps = [["Fetch the pull request", reading], ["Read the diff with the model", false]];  // [label, finished]
    const current = reading ? 1 : 0;
    return html`<section class="pony-progress" role="status" aria-live="polite" aria-label="Ponytail review in progress">
      <ol>${steps.map(([label, finished], i) => html`<li key=${i} class=${finished ? "done" : i === current ? "now" : "next"}>
        <span class="pony-mark">${finished ? html`<${Icon} name="check" size=${13} />` : i === current ? html`<span class="spinner small"></span>` : null}</span>${label}</li>`)}</ol>
      <span class="muted">${Math.max(0, Math.round(now - (job.created || now)))} s</span>
    </section>`;
  }

  // The list's view of a pull request's Ponytail review.
  function PonyChip({ pr, pony }) {
    if (pony?.status === "running" && pony.number === pr.number) {
      return html`<div class="pony-chip"><span class="spinner small"></span>Reviewing</div>`;
    }
    if (!pr.ponytail) return null;
    const n = pr.ponytail.findings.length;
    return html`<div class="pony-chip done" title=${n ? `${n} Ponytail finding${n === 1 ? "" : "s"}` : "Ponytail found nothing to cut"}>Ponytail: ${n || "lean"}</div>`;
  }

  // A transient error: it can be closed, and it closes itself, so it never sits on the page for good.
  function Alert({ message, onClose, seconds = 10, className = "", style }) {
    useEffect(() => {
      const timer = setTimeout(onClose, seconds * 1000);
      return () => clearTimeout(timer);
    }, [message]);
    return html`<div class=${"error-box alert " + className} role="alert" style=${style}>
      <span>${message}</span>
      <button class="icon-btn" type="button" onClick=${onClose} aria-label="Dismiss"><${Icon} name="x" size=${16} /></button></div>`;
  }

  function Home({ index }) {
    const ctx = React.useContext(AppCtx);
    const job = ctx.job;
    const showJob = job && VERDICT_JOBS.includes(job.command) && ctx.dismissed !== job.started;
    const [hidden, setHidden] = useState(() => { try { return JSON.parse(store.get("pm-hidden-recent", "[]")); } catch { return []; } });
    const hide = (ids) => { const next = [...new Set([...hidden, ...ids])].slice(-200); setHidden(next); store.set("pm-hidden-recent", JSON.stringify(next)); };
    const all = (index?.runs || []).filter((r) => !r.error).slice().sort((a, b) => b.id.localeCompare(a.id));
    const recent = all.filter((r) => !hidden.includes(r.id)).slice(0, 6);
    const missing = ctx.health ? [!ctx.health.docker_running && "Docker", !ctx.health.runner_image && "sandbox image",
      !ctx.health.model_key && "AI model"].filter(Boolean) : [];
    return html`<div class=${"home" + (recent.length ? " has-recent" : "")}>
      <div class="home-main">
      <section class="hero">
        <h1>Check a change before you merge it</h1>
        <p class="lede">Pick your project and the branch with the change. ProofMerge writes tests that try to break it,
          runs them in a sealed sandbox, and tells you whether it is safe to merge, with the evidence to show why.</p>
      </section>
      ${missing.length && !showJob ? html`<div class="setup-nudge panel">
        <${Icon} name="alert" />
        <span>Before your first check, set up: <strong>${missing.join(", ")}</strong>.</span>
        <button class="ghost" type="button" onClick=${() => ctx.go({ tab: "setup" })}>Open Setup</button></div>` : null}
      ${showJob ? html`
        <${Pipeline} job=${job} />
        ${job.status === "done" && job.command === "verify" ? html`<${VerdictReveal} job=${job} />` : null}
        ${job.status === "done" && job.command === "hypotheses" ? html`<${Risks} job=${job} />` : null}
        ${job.status === "done" && job.command === "probe" ? html`<section class="reveal panel"><h2>Tests ran on both branches</h2>
          <pre>${job.output}</pre><div class="actions"><button class="primary" type="button" onClick=${ctx.dismiss}>Check another change</button></div></section>` : null}`
        : html`<${Composer} />`}
      </div>
      ${recent.length ? html`<aside class="recent" aria-labelledby="recent-title">
        <div class="section-head"><h2 id="recent-title">Recent checks</h2>
          <span><button class="link" type="button" onClick=${() => hide(recent.map((r) => r.id))}>Clear</button>
            <button class="link" type="button" onClick=${() => ctx.go({ tab: "runs" })}>See all ${index.runs.length}</button></span></div>
        <ol class="recent-list">${recent.map((r, i) => html`<li key=${r.id} style=${{ "--i": i }}>
          <button type="button" class=${r.decision} onClick=${() => ctx.go({ tab: "runs", run: r.id })}>
            <span class="recent-top"><span class=${"pill " + r.decision}>${r.decision}</span>
              <span class="muted mono">${short(r.base_sha)} → ${short(r.head_sha)}</span></span>
            <span class="run-name">${r.id}</span>
            ${r.reasons?.[0] ? html`<span class="recent-why">${r.reasons[0]}</span>` : null}
          </button>
          <button class="icon-btn recent-x" type="button" onClick=${() => hide([r.id])} aria-label=${"Remove " + r.id + " from recent checks"}
            title="Remove from this list (the run stays under Runs)"><${Icon} name="x" size=${14} /></button></li>`)}</ol>
      </aside>` : null}
    </div>`;
  }

  // ---------------------------------------------------------------- GitHub sign-in and repositories
  const DEVICE_URL = "https://github.com/login/device"; // fixed: never a URL from the server
  const REPO_INPUT = /^(?:https?:\/\/github\.com\/)?([A-Za-z0-9-]{1,39}\/[A-Za-z0-9._-]{1,100}?)(?:\.git)?\/?$/;
  const parseRepo = (text) => {
    const m = text.trim().match(REPO_INPUT);
    return m && !/\/\.\.?$/.test(m[1]) ? m[1] : null;
  };
  const ago = (iso) => {
    const days = Math.floor((Date.now() - Date.parse(iso)) / 864e5);
    return !iso || Number.isNaN(days) ? "" : days < 1 ? "today" : days === 1 ? "yesterday"
      : days < 30 ? `${days} days ago` : days < 60 ? "a month ago" : days < 365 ? `${Math.floor(days / 30)} months ago`
      : days < 730 ? "a year ago" : `${Math.floor(days / 365)} years ago`;
  };

  // GitHub state from the server; polls only while sign-in waits or a download runs.
  function useGitHub() {
    const { token } = React.useContext(AppCtx);
    const [gh, setGh] = useState(null);
    const [error, setError] = useState("");
    const [reads, setReads] = useState(0);
    const load = useCallback(() => authGet("/api/github").then((d) => { setGh(d); setError(""); })
      .catch((e) => setError(e.message)).finally(() => setReads((n) => n + 1)), []);
    useEffect(() => { if (token) load(); }, [token]);
    const waiting = gh?.login?.state === "waiting";
    const busy = waiting || gh?.clone?.state === "running";
    useEffect(() => {
      if (!busy) return;
      const timer = setTimeout(load, waiting ? 2000 : 1000);  // cheap: the server reuses its sign-in answer
      return () => clearTimeout(timer);
    }, [reads, busy]);
    const act = (url, body = {}) => post(url, body).then((d) => { setGh(d); setReads((n) => n + 1); return d; });
    return { gh, error, load, act };
  }

  function GitHubRow() {
    const { gh, error, load, act } = useGitHub();
    const [pending, setPending] = useState(false);
    const [fail, setFail] = useState("");
    const run = (url) => {
      setPending(true); setFail("");
      act(url).catch((e) => setFail(e.message)).finally(() => setPending(false));
    };
    if (!gh) {
      return html`<${SetupRow} optional=${true} icon="pull" title="GitHub">${error || "Checking…"}</${SetupRow}>`;
    }
    const login = gh.login;
    let body;
    let action = null;
    if (gh.error) {
      body = gh.error;
      action = html`<button class="ghost" type="button" disabled=${pending} onClick=${() => { setPending(true); load().finally(() => setPending(false)); }}>Try again</button>`;
    } else if (!gh.gh) {
      body = "To open pull requests from GitHub, ProofMerge needs the GitHub CLI. Run the ProofMerge installer again and it can add it for you.";
      action = html`<a class="ghost" href="https://cli.github.com/" target="_blank" rel="noopener noreferrer">Get GitHub CLI</a>`;
    } else if (gh.user) {
      const others = (gh.accounts || []).filter((a) => !a.active);
      body = html`Signed in as <strong class="gh-user">@${gh.user}</strong>. Pick a repository under Pull requests.
        ${others.length ? html`<div class="gh-accounts" role="group" aria-label="Switch GitHub account">
          <span class="muted">Switch to</span>
          ${others.map((a) => html`<button key=${a.login} class="ghost btn-small" type="button" disabled=${pending}
            onClick=${() => { setPending(true); setFail(""); act("/api/github/switch", { user: a.login }).catch((e) => setFail(e.message)).finally(() => setPending(false)); }}>@${a.login}</button>`)}
        </div>
        <p class="muted fix-note">Switching also changes the account the <code>gh</code> command uses in a terminal.</p>` : null}`;
      action = html`<button class="ghost" type="button" disabled=${pending} onClick=${() => run("/api/github/add")}>Add another account</button>
        <button class="ghost" type="button" disabled=${pending} onClick=${() => run("/api/github/logout")}>Sign out @${gh.user}</button>`;
    } else if (login?.state === "waiting") {
      body = html`<div class="device" aria-live="polite">
        ${login.code
          ? html`<div class="device-code-row"><span class="device-code">${login.code}</span><${Copy} text=${login.code} label="the code" /></div>`
          : html`<div class="device-code-row"><span class="spinner" aria-hidden="true"></span><span>Getting a code…</span></div>`}
        <p>Open GitHub, paste the code and press <strong>Authorize</strong>. This page updates by itself.</p>
        <a class="primary" href=${DEVICE_URL} target="_blank" rel="noopener noreferrer">Open GitHub</a>
      </div>`;
      action = html`<button class="ghost" type="button" disabled=${pending} onClick=${() => run("/api/github/login/cancel")}>Cancel</button>`;
    } else {
      body = login?.state === "failed" ? login.error
        : "Sign in to pick a repository and check its pull requests. You approve it on github.com; ProofMerge never sees your password.";
      action = html`<button class="primary" type="button" disabled=${pending} onClick=${() => run("/api/github/login")}>
        ${login?.state === "failed" ? "Try again" : "Sign in to GitHub"}</button>`;
    }
    return html`<${SetupRow} ok=${!!gh.user} optional=${true} icon="pull" title="GitHub" action=${action}>
      ${body}${fail ? html`<${Alert} message=${fail} onClose=${() => setFail("")} className="gh-error" />` : null}</${SetupRow}>`;
  }

  function RepoPicker({ onPicked, onCancel }) {
    const ctx = React.useContext(AppCtx);
    const { gh, error, load, act } = useGitHub();
    const [repos, setRepos] = useState(null);
    const [listError, setListError] = useState("");
    const [query, setQuery] = useState("");
    const [other, setOther] = useState("");
    const [fail, setFail] = useState("");
    const [gone, setGone] = useState("");  // a clone failure that was closed
    const asked = useRef("");
    const signedIn = !!gh?.user;
    const loadRepos = () => {
      setListError("");
      authGet("/api/github/repos").then((d) => setRepos(d.repos)).catch((e) => setListError(e.message));
    };
    useEffect(() => { if (signedIn) loadRepos(); }, [signedIn]);
    const clone = gh?.clone;
    useEffect(() => {
      // Once the download we asked for finishes, make it the project; the PR list follows it.
      if (!clone || clone.repo !== asked.current || clone.state === "running") return;
      asked.current = "";
      if (clone.state === "done") ctx.saveSettings({ project: clone.path }).then(onPicked).catch((e) => setFail(e.message));
    }, [gh]);
    const [starting, setStarting] = useState("");
    const pick = (name) => {
      setFail(""); asked.current = name; setStarting(name);
      act("/api/github/clone", { repo: name }).catch((e) => { asked.current = ""; setFail(e.message); })
        .finally(() => setStarting(""));
    };
    const otherName = parseRepo(other);
    const back = onCancel ? html`<button class="ghost" type="button" onClick=${onCancel}>Keep the current repository</button>` : null;
    if (!gh) return error ? html`<div class="error-box">${error}</div>` : html`<p class="boot">Checking GitHub…</p>`;
    if (gh.error) {
      return html`<div class="panel empty picker-empty"><strong>GitHub is not answering</strong>${gh.error}
        <div class="actions center"><button class="primary" type="button" onClick=${load}>Try again</button>${back}</div></div>`;
    }
    if (!gh.gh || !gh.user) {
      return html`<div class="panel empty picker-empty"><strong>Sign in to GitHub first</strong>
        Then pick any of your repositories here and check its pull requests, no terminal needed.
        <div class="actions center">
          <button class="primary" type="button" onClick=${() => ctx.go({ tab: "setup" })}>Go to Setup</button>
          <button class="ghost" type="button" onClick=${() => ctx.go({ tab: "home" })}>Use a folder on this computer</button>
          ${back}</div></div>`;
    }
    if (starting || clone?.state === "running") {
      return html`<div class="panel clone-progress" role="status" aria-live="polite">
        <div class="clone-head"><span class="spinner" aria-hidden="true"></span><strong>Downloading ${starting || clone.repo}…</strong></div>
        <p class="muted">The first time takes a minute for a big repository. Next time ProofMerge only fetches what changed.</p>
        <div class="bar" aria-hidden="true"><span></span></div></div>`;
    }
    const q = query.trim().toLowerCase();
    const shown = (repos || []).filter((r) => !q || `${r.name} ${r.description}`.toLowerCase().includes(q));
    const failed = fail || (clone?.state === "failed" ? clone.error : "");
    return html`<section class="panel picker">
      <div class="picker-head">
        <div><strong>Choose a repository</strong>
          <div class="muted">Signed in as @${gh.user}. ProofMerge downloads it to your ProofMerge folder; its code only ever runs in the sandbox.</div></div>
        ${back}
      </div>
      ${failed && failed !== gone ? html`<${Alert} message=${failed} onClose=${() => { setFail(""); setGone(failed); }} />` : null}
      <input class="search" type="search" placeholder="Search your repositories" value=${query}
        onInput=${(e) => setQuery(e.target.value)} aria-label="Search your repositories" />
      ${listError ? html`<div class="error-box">${listError} <button class="ghost" type="button" onClick=${loadRepos}>Try again</button></div>`
        : !repos ? html`<p class="boot">Loading your repositories…</p>`
        : shown.length ? html`<ul class="repo-list">${shown.map((r) => html`<li key=${r.name}>
            <button class="repo-item" type="button" onClick=${() => pick(r.name)}>
              <span class="repo-name">${r.name}${r.private ? html`<span class="tag">Private</span>` : null}</span>
              ${r.description ? html`<span class="repo-desc">${r.description}</span>` : null}
              <span class="repo-when">${ago(r.updated)}</span>
            </button></li>`)}</ul>`
        : html`<p class="muted">${repos.length ? "No repository matches. Change the search." : "Your account has no repositories yet."}</p>`}
      <form class="repo-other" onSubmit=${(e) => { e.preventDefault(); if (otherName) pick(otherName); }}>
        <label for="repo-other">Another repository</label>
        <div class="repo-other-row">
          <input id="repo-other" class="search" type="text" spellcheck="false" autocomplete="off"
            placeholder="owner/name or https://github.com/owner/name" value=${other} onInput=${(e) => setOther(e.target.value)} />
          <button class="ghost" type="submit" disabled=${!otherName}>Open</button>
        </div>
        ${other.trim() && !otherName ? html`<span class="muted">Use owner/name, like octocat/hello-world.</span>` : null}
      </form>
    </section>`;
  }

  function SetupRow({ ok, optional, icon, title, children, action }) {
    return html`<li class=${"setup-row " + (ok ? "ok" : optional ? "optional" : "todo")}>
      <span class="status-dot" aria-label=${ok ? "Ready" : optional ? "Optional" : "Needs setup"}><${Icon} name=${ok ? "check" : icon} size=${16} /></span>
      <div class="setup-text"><strong>${title}</strong><div class="muted">${children}</div></div>
      <div class="setup-action">${action}</div>
    </li>`;
  }

  // Which model writes and checks the tests. The choice is saved; a key never is.
  const PROVIDERS = [["meta", "Meta Muse"], ["claude", "Claude plan"], ["openai", "Other API"]];

  function ModelRow() {
    const ctx = React.useContext(AppCtx);
    const h = ctx.health;
    const chosen = ctx.settings.model || {};
    const [provider, setProvider] = useState(chosen.provider || h.provider || "meta");
    const [form, setForm] = useState({ base_url: chosen.base_url || "", spark_model: chosen.spark_model || "",
      wonder_model: chosen.wonder_model || "" });
    const [key, setKey] = useState("");
    const [note, setNote] = useState("");
    const [saving, setSaving] = useState(false);
    const field = (name) => (e) => setForm((f) => ({ ...f, [name]: e.target.value }));
    const save = (body, done) => {
      setSaving(true); setNote("");
      return ctx.saveSettings(body).then(() => { setNote({ text: done }); ctx.reloadHealth(); })
        .catch((e) => setNote({ text: e.message, bad: true })).finally(() => setSaving(false));
    };
    const choose = (next) => {
      setProvider(next);
      if (next !== "openai") save({ model: { provider: next } }, "Saved.");
    };
    const saveApi = (event) => {
      event.preventDefault();
      save({ model: { provider: "openai", ...form }, ...(key.trim() ? { key } : {}) },
        "Saved. The key stays in memory until you close ProofMerge.").then(() => setKey(""));
    };
    const saveKey = (event) => {
      event.preventDefault();
      save({ key }, "Saved for this session.").then(() => setKey(""));
    };
    const live = h.provider === provider;
    const ok = live && h.model_key;
    const keyNote = ctx.settings.key_in_env ? "A key is set in your environment."
      : ctx.settings.key_set ? "A key is saved for this session." : "";
    const keyInput = html`<input class="search" type="password" autocomplete="off" spellcheck="false"
      placeholder=${ctx.settings.key_set ? "Replace the key" : "Paste your API key"} value=${key}
      onInput=${(e) => setKey(e.target.value)} aria-label="API key" />`;
    let body;
    if (provider === "meta") {
      body = html`<p class="model-help">Meta's Muse Spark writes the tests and Muse Wonder checks them. The results in
        this project were measured with them. Paste your Meta developer key; it stays in memory until you close
        ProofMerge and is never saved to disk.</p>
        ${ctx.settings.key_in_env && ctx.settings.key_set
          ? html`<p class="model-help">A key saved for this session is used instead of the one in your environment.</p>`
          : keyNote ? html`<p class="model-help">${keyNote}</p>` : null}
        ${!ctx.settings.key_in_env || ctx.settings.key_set ? html`<form class="key-form" onSubmit=${saveKey}>${keyInput}
          <button class="ghost" type="submit" disabled=${!key.trim() || saving}>Save</button>
          ${ctx.settings.key_in_env ? html`<button class="ghost" type="button" disabled=${saving}
            onClick=${() => save({ key: "" }, "Using the key from your environment.")}>Use my environment key</button>` : null}
          </form>` : null}`;
    } else if (provider === "claude") {
      body = html`<p class="model-help">Uses the Claude plan (Pro or Max) that Claude Code is signed in to on this
        computer. No API key needed. ProofMerge calls Claude Code with every tool switched off, so the model only
        reads and answers.</p>
        ${live && !h.claude_cli ? html`<p class="model-help warn">Claude Code is not installed here. Install it, open it
          once to sign in, then press Check again. <a href="https://claude.com/claude-code" target="_blank"
          rel="noopener noreferrer">Get Claude Code</a></p>` : null}`;
    } else {
      body = html`<p class="model-help">Any service with an OpenAI-compatible API, or a model running on this computer
        (http://localhost). Your code and the change are sent to this address.</p>
        <form class="model-form" onSubmit=${saveApi}>
          <label>Address<input class="search" type="url" spellcheck="false" placeholder="https://api.example.com/v1"
            value=${form.base_url} onInput=${field("base_url")} /></label>
          <div class="model-pair">
            <label>Model that writes the tests<input class="search" spellcheck="false" placeholder="model name"
              value=${form.spark_model} onInput=${field("spark_model")} /></label>
            <label>Model that checks them<input class="search" spellcheck="false" placeholder="model name"
              value=${form.wonder_model} onInput=${field("wonder_model")} /></label>
          </div>
          <label>API key ${keyNote ? html`<span class="muted">(${keyNote.toLowerCase()})</span>` : null}${keyInput}</label>
          <div><button class="primary" type="submit"
            disabled=${saving || !form.base_url.trim() || !form.spark_model.trim() || !form.wonder_model.trim()}>Save</button></div>
        </form>`;
    }
    return html`<${SetupRow} ok=${ok} icon="key" title="AI model"
      action=${ok ? html`<button class="ghost" type="button" disabled=${ctx.busy} onClick=${() => ctx.start({ command: "smoke" })}>Test connection</button>` : null}>
      <div class="modes model-modes" role="radiogroup" aria-label="AI model">
        ${PROVIDERS.map(([value, label]) => html`<button key=${value} type="button" role="radio"
          aria-checked=${provider === value} disabled=${saving} onClick=${() => choose(value)}>${label}</button>`)}
      </div>
      ${body}
      ${note ? html`<p class=${"model-help" + (note.bad ? " bad" : "")} role=${note.bad ? "alert" : "status"}>${note.text}</p>` : null}
      ${provider !== "meta" ? html`<p class="model-help faint">The verdict rules are the same for every model. How good the
        tests are was measured with Meta Muse only.</p>` : null}
    </${SetupRow}>`;
  }

  function SetupView() {
    const ctx = React.useContext(AppCtx);
    const h = ctx.health;
    if (!h) return html`<p class="boot">Checking this computer…</p>`;
    const ready = h.git && h.docker_running && h.runner_image && h.model_key;
    return html`
      <section class="hero small">
        <h1>${ready ? "Ready to check changes" : "Set up ProofMerge"}</h1>
        <p class="lede">${ready ? "Everything ProofMerge needs is in place." : "A few things need to be in place before the first check. Do them in order; each one shows a tick when it works."}</p>
      </section>
      <ol class="setup-list panel">
        <${SetupRow} ok=${h.git} icon="git" title="Git"
          action=${h.git ? null : html`<a class="ghost" href="https://git-scm.com/downloads" target="_blank" rel="noopener noreferrer">Get Git</a>`}>
          ${h.git ? "Installed." : "ProofMerge reads your branches with Git. Install it, then press Check again."}</${SetupRow}>
        <${SetupRow} ok=${h.docker_running} icon="box" title="Docker Desktop"
          action=${h.docker_installed ? null : html`<a class="ghost" href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noopener noreferrer">Get Docker Desktop</a>`}>
          ${h.docker_running ? "Running. The tests run inside it, away from your files."
            : h.docker_installed ? "Installed but not running. Open Docker Desktop and wait until it says it is running, then press Check again."
            : "The tests run inside Docker, so the change never touches your computer directly."}</${SetupRow}>
        <${SetupRow} ok=${h.runner_image} icon="box" title="Sandbox image"
          action=${h.runner_image ? null : html`<button class="primary" type="button" disabled=${!h.docker_running || ctx.busy}
            onClick=${() => ctx.start({ command: "setup" })}>Build it</button>`}>
          ${h.runner_image ? "Built." : "A one-time build of the sealed test environment. It takes a few minutes and needs Docker running."}</${SetupRow}>
        <${ModelRow} />
        <${GitHubRow} />
      </ol>
      <div class="actions"><button class="ghost" type="button" onClick=${ctx.reloadHealth}><${Icon} name="refresh" />Check again</button></div>
      <dl class="facts about">
        <dt>version</dt><dd>ProofMerge ${h.version}, Python ${h.python}</dd>
        <dt>runs are saved in</dt><dd><code>${h.runs_folder}</code></dd>
        <dt>project</dt><dd>${ctx.settings.project ? html`<code>${ctx.settings.project}</code>` : "not chosen yet"}</dd>
      </dl>`;
  }

  function JobDock() {
    const ctx = React.useContext(AppCtx);
    const job = ctx.job;
    if (!job || ctx.dismissed === job.started) return null;
    if (VERDICT_JOBS.includes(job.command) && ctx.tab === "home") return null;
    const running = job.status === "running";
    const ok = job.status === "done";
    return html`<aside class=${"dock panel " + (running ? "running" : ok ? "ok" : "bad")} role="status" aria-live="polite">
      <div class="dock-head">
        <span class="dock-icon">${running ? html`<span class="spinner" aria-hidden="true"></span>` : html`<${Icon} name=${ok ? "check" : "alert"} />`}</span>
        <strong>${running ? JOB_TITLE[job.command] : ok ? JOB_TITLE[job.command] + ": done" : JOB_TITLE[job.command] + ": " + (job.status === "failed" ? "failed" : job.status)}</strong>
        ${running ? html`<button class="ghost small" type="button" onClick=${ctx.stop}>Stop</button>`
          : html`<button class="icon-btn" type="button" onClick=${ctx.dismiss} aria-label="Dismiss"><${Icon} name="x" size=${16} /></button>`}
      </div>
      ${running ? html`<div class="bar" aria-hidden="true"><span></span></div>` : null}
      <p class="dock-line mono">${(job.output || "").match(/^(verdict: .*|evidence intact.*|built runner image.*)$/m)?.[0]
        || lastLine(job.output) || "Starting…"}</p>
      ${VERDICT_JOBS.includes(job.command) ? html`<button class="link" type="button" onClick=${() => ctx.go({ tab: "home" })}>${running ? "Show progress" : "Show the result"}</button>` : null}
      <details><summary>Full output</summary><pre>${job.output || "(nothing yet)"}</pre></details>
    </aside>`;
  }

  const NAV = [["home", "Home", "home"], ["runs", "Runs", "list"], ["prs", "Pull requests", "pull"],
    ["bench", "Benchmarks", "chart"], ["setup", "Setup", "sliders"]];

  function App() {
    const [route, setRoute] = useState(readHash);
    const [index, setIndex] = useState(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [theme, setTheme] = useState(() => store.get("pm-theme", "auto"));
    const [session, setSession] = useState({ token: "", prs: false, settings: {} });
    const [health, setHealth] = useState(null);
    const [job, setJob] = useState(null);
    const [dismissed, setDismissed] = useState(() => Number(store.get("pm-dismissed", "0")));
    const [notice, setNotice] = useState("");
    const [quitArmed, setQuitArmed] = useState(false);
    const [quit, setQuit] = useState(false);
    const [picking, setPicking] = useState(false);
    const reloadHealth = useCallback(() => authGet("/api/health").then(setHealth).catch(() => {}), []);
    useEffect(() => {
      getJSON("/api/session").then((s) => {
        TOKEN = s.token; setSession(s); reloadHealth();
        authGet("/api/job").then((d) => setJob(d.job)).catch(() => {});
      }).catch((e) => setError(e.message));
    }, []);
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
    // While a job runs, follow it; when it ends, pick up the new run and any new setup state.
    const wasRunning = useRef(false);
    useEffect(() => {
      const running = job?.status === "running";
      if (wasRunning.current && !running) { load(); reloadHealth(); }
      wasRunning.current = running;
      if (!running) return;
      let active = true;
      let timer;
      const poll = () => authGet("/api/job").then((d) => { if (active) setJob(d.job); })
        .catch(() => { if (active) timer = setTimeout(poll, 1200); });
      timer = setTimeout(poll, 1200);
      return () => { active = false; clearTimeout(timer); };
    }, [job]);
    useEffect(() => {
      const onHash = () => setRoute(readHash());
      addEventListener("hashchange", onHash);
      return () => removeEventListener("hashchange", onHash);
    }, []);
    const go = (next) => { const state = { ...route, ...next }; setRoute(state); writeHash(state); scrollTo({ top: 0 }); };
    useEffect(() => {
      if (index && route.tab === "runs" && !route.run && index.runs.length) go({ run: [...index.runs].sort(SORTS.verdict)[0].id });
    }, [index, route.tab]);
    useEffect(() => { if (!quitArmed) return; const t = setTimeout(() => setQuitArmed(false), 3000); return () => clearTimeout(t); }, [quitArmed]);
    const start = (body) => post("/api/jobs", body).then((d) => { setJob(d.job); setNotice(""); })
      .catch((e) => setNotice(e.message));
    const stop = () => post("/api/jobs/stop", {}).then((d) => setJob(d.job)).catch((e) => setNotice(e.message));
    const saveSettings = (body) => post("/api/settings", body).then((d) => {
      setSession((s) => ({ ...s, settings: d.settings, prs: d.prs })); return d;
    });
    const dismiss = () => { if (job) { setDismissed(job.started); store.set("pm-dismissed", String(job.started)); } };
    const ctx = { settings: session.settings || {}, health, job, busy: job?.status === "running", start, stop, go,
      saveSettings, reloadHealth, dismiss, dismissed, tab: route.tab, token: session.token };
    const project = session.settings?.project || "";
    const nextTheme = { auto: "light", light: "dark", dark: "auto" }[theme];
    if (quit) {
      return html`<div class="quit"><${Icon} name="power" size=${28} /><h1>ProofMerge has stopped</h1>
        <p class="muted">You can close this tab. Open ProofMerge again from its shortcut when you need it.</p></div>`;
    }
    return html`<${AppCtx.Provider} value=${ctx}>
      <div class="shell">
        <aside class="side">
          <div class="brand">
            <img class="mark" src="/static/legacy/icon.svg" alt="" width="24" height="24" /><span class="brand-name">ProofMerge</span></div>
          <nav class="nav" aria-label="Sections">
            ${NAV.map(([key, label, icon]) => html`<button key=${key} type="button" class="nav-item"
              aria-current=${route.tab === key ? "page" : undefined} onClick=${() => go({ tab: key })}>
              <${Icon} name=${icon} /><span>${label}</span>
              ${key === "setup" && health && !(health.docker_running && health.runner_image && health.model_key) ? html`<span class="badge" aria-label="needs setup"></span>` : null}
              ${key === "home" && job?.status === "running" ? html`<span class="spinner small" aria-label="running"></span>` : null}
            </button>`)}
          </nav>
          <div class="side-foot">
            <button class="nav-item" type="button" onClick=${() => setTheme(nextTheme)} aria-label=${"Theme: " + theme + ". Switch to " + nextTheme}>
              <${Icon} name="sun" /><span>Theme: ${theme}</span></button>
            <button class=${"nav-item" + (quitArmed ? " armed" : "")} type="button"
              onClick=${() => quitArmed ? post("/api/quit", {}).finally(() => setQuit(true)) : setQuitArmed(true)}>
              <${Icon} name="power" /><span>${quitArmed ? "Click again to quit" : "Quit ProofMerge"}</span></button>
          </div>
        </aside>
        <main class="page" key=${route.tab}>
          ${notice ? html`<div class="error-box notice" role="alert">${notice}<button class="icon-btn" type="button" onClick=${() => setNotice("")} aria-label="Dismiss"><${Icon} name="x" size=${16} /></button></div>` : null}
          ${fresh ? html`<div class="banner" role="status">${fresh} new run${fresh === 1 ? "" : "s"} in this folder.
            <button class="primary" type="button" onClick=${load}>Show them</button></div>` : null}
          ${error ? html`<div class="error-box">Could not reach ProofMerge: ${error}. If you closed it, open it again from its shortcut.</div>` : null}
          ${route.tab === "home" ? html`<${Home} index=${index} />` : null}
          ${route.tab === "setup" ? html`<${SetupView} />` : null}
          ${!index && !error && !["home", "setup"].includes(route.tab) ? html`<p class="boot">Loading runs…</p>` : null}
          ${index && route.tab === "runs" ? html`<div class="page-head"><h1>Runs</h1>
              <button class="ghost" type="button" onClick=${load} disabled=${loading}><${Icon} name="refresh" />${loading ? "Scanning…" : "Refresh"}</button></div>
            <${RunsView} index=${index} selected=${route.run} onSelect=${(run) => go({ run })} />` : null}
          ${index && route.tab === "bench" ? html`<${BenchmarksView} index=${index} onOpenRun=${(run) => go({ tab: "runs", run })} />` : null}
          ${index && route.tab === "prs" ? html`<div class="page-head"><h1>Pull requests</h1>
              ${session.prs && !picking ? html`<div class="repo-now">
                <span class="muted">Repository</span><code title=${project}>${project.split(/[\\/]/).slice(-2).join("/")}</code>
                <button class="ghost" type="button" onClick=${() => setPicking(true)}>Change repository</button></div>` : null}</div>
            ${!session.prs || picking
              ? html`<div class="swap" key="picker"><${RepoPicker} onPicked=${() => setPicking(false)}
                  onCancel=${session.prs ? () => setPicking(false) : null} /></div>`
              : html`<div class="swap" key=${"prs:" + project}><${PullsView} index=${index} token=${session.token} selected=${route.pr}
                  onSelect=${(pr) => go({ pr: String(pr) })} onOpenRun=${(run) => { load(); go({ tab: "runs", run }); }}
                  onRunsChanged=${load} /></div>`}` : null}
        </main>
        <${JobDock} />
      </div>
    </${AppCtx.Provider}>`;
  }

  ReactDOM.createRoot(document.getElementById("root")).render(html`<${App} />`);
})();
