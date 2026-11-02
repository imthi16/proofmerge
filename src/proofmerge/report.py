"""report.html: one self-contained evidence document rendered from proof.json.

Every string in proof.json comes from the PR and the model, so all of it is
escaped before it enters the page. The file is static: inline CSS, no scripts,
no external resources.
"""

import base64
import html
import shlex
from functools import cache
from pathlib import Path

from proofmerge.evidence import Experiment, Finding, ModelUsage, Proof
from proofmerge.intent import IntentClaim

MAX_FAILURE_LINES = 20

# PASS is scoped, never a claim of safety or correctness (verdict contract).
_PASS_PHRASE = "No regression found within the tested scope"
_HEADLINE = {
    "PASS": _PASS_PHRASE,
    "REVIEW": "Not conclusive — a human must review",
    "BLOCK": "Verified regression — do not merge",
}

_CSS = """\
:root {
  color-scheme: dark;
  --bg: #090c11; --surface: #0f141b; --raised: #151b24;
  --rule: #1f2732; --rule-strong: #2c3644;
  --fg: #e9eef5; --muted: #8b97a9; --faint: #75818f;
  --brand: #8b9cff; --pass: #34d399; --review: #fbbf24; --block: #ff5d5d;
  --glow-a: rgba(139, 156, 255, .12);
}
@media (prefers-color-scheme: light) {
  :root {
    color-scheme: light;
    --bg: #f5f6f8; --surface: #ffffff; --raised: #f0f2f5;
    --rule: #e2e6ec; --rule-strong: #cdd3dc;
    --fg: #0e131a; --muted: #5b6676; --faint: #5f6a7a;
    --brand: #3f4fd0; --pass: #0b7550; --review: #855000; --block: #b42323;
    --glow-a: rgba(75, 92, 228, .07);
  }
}
:root {
  --pass-bg: color-mix(in oklab, var(--pass) 14%, transparent);
  --review-bg: color-mix(in oklab, var(--review) 14%, transparent);
  --block-bg: color-mix(in oklab, var(--block) 14%, transparent);
  --pass-line: color-mix(in oklab, var(--pass) 45%, transparent);
  --review-line: color-mix(in oklab, var(--review) 45%, transparent);
  --block-line: color-mix(in oklab, var(--block) 45%, transparent);
  --select: color-mix(in oklab, var(--brand) 32%, transparent);
  --r-sm: 6px; --r-md: 14px;
  --ease: cubic-bezier(.16, 1, .3, 1);
  --sans: "Geist", ui-sans-serif, system-ui, sans-serif;
  --mono: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  accent-color: var(--brand);
}
* { box-sizing: border-box; scrollbar-color: var(--rule-strong) transparent; }
::selection { background: var(--select); color: var(--fg); }
:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; border-radius: var(--r-sm); }
body { margin: 0; color: var(--fg); font: 15.5px/1.6 var(--sans); -webkit-font-smoothing: antialiased;
  background: radial-gradient(56rem 20rem at 20% -8rem, var(--glow-a), transparent 70%) no-repeat, var(--bg); }
main { max-width: 58rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
p { margin: .5rem 0; text-wrap: pretty; }
code, pre, .sha { font-family: var(--mono); font-size: .86em; font-variant-ligatures: none; }
code { background: var(--raised); border: 1px solid var(--rule); padding: .02rem .32rem; border-radius: 5px;
  overflow-wrap: anywhere; }
pre { background: var(--bg); border: 1px solid var(--rule); padding: .9rem 1rem; border-radius: var(--r-sm);
  margin: .5rem 0 1rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
pre code { background: none; border: 0; padding: 0; font-size: 1em; }
.muted { color: var(--muted); }
h2 { font-size: 1.4rem; font-weight: 600; letter-spacing: -.025em; margin: 3rem 0 1rem; padding-top: 1.25rem;
  border-top: 1px solid var(--rule); text-wrap: balance; }
h3 { font-size: 1.1rem; font-weight: 600; letter-spacing: -.02em; line-height: 1.35; margin: 0 0 .5rem; text-wrap: balance; }
h4 { font-size: .85rem; font-weight: 600; color: var(--muted); margin: 1.4rem 0 .3rem; }
blockquote { margin: .5rem 0; padding: .5rem .85rem; background: var(--raised); border-radius: var(--r-sm);
  font-style: italic; color: var(--muted); }

/* The verdict: the one loud element on the page. */
.banner { position: relative; overflow: hidden; border-radius: var(--r-md); padding: 1.75rem 1.75rem 1.5rem;
  border: 1px solid var(--rule); background: var(--surface); }
.banner::before { content: ""; position: absolute; inset: 0 0 auto; height: 3px; }
.banner.pass { background: linear-gradient(180deg, var(--pass-bg), transparent 60%), var(--surface); border-color: var(--pass-line); }
.banner.pass::before { background: var(--pass); }
.banner.review { background: linear-gradient(180deg, var(--review-bg), transparent 60%), var(--surface); border-color: var(--review-line); }
.banner.review::before { background: var(--review); }
.banner.block { background: linear-gradient(180deg, var(--block-bg), transparent 60%), var(--surface); border-color: var(--block-line); }
.banner.block::before { background: var(--block); }
.banner .decision { font-size: clamp(3rem, 11vw, 5.5rem); font-weight: 700; letter-spacing: -.06em; line-height: .88;
  margin: 0; animation: decision-open 1s var(--ease) both; }
.banner.pass .decision { color: var(--pass); }
.banner.review .decision { color: var(--review); }
.banner.block .decision { color: var(--block); }
@keyframes decision-open { from { clip-path: inset(0 0 100% 0); transform: translateY(.15em); }
  to { clip-path: inset(0 0 -10% 0); transform: none; } }
.banner .headline { font-size: 1.3rem; font-weight: 600; letter-spacing: -.025em; line-height: 1.25; margin: .9rem 0 0;
  text-wrap: balance; }
.banner .run { margin: .6rem 0 0; font-size: .88rem; color: var(--muted); }
.banner ul { margin: 1.1rem 0 0; padding: 1rem 0 0 1.2rem; border-top: 1px solid var(--rule); color: var(--muted); }
.banner li::marker { color: var(--faint); }
.banner li + li { margin-top: .3rem; }

.card { background: var(--surface); border: 1px solid var(--rule); border-radius: var(--r-md);
  padding: 1.4rem 1.5rem 1.1rem; margin: 0 0 1.25rem;
  box-shadow: 0 1px 0 rgba(255, 255, 255, .03) inset, 0 16px 40px -20px rgba(0, 0, 0, .5); }
.card h3 code { font-size: .78em; margin-right: .4rem; color: var(--brand);
  border-color: color-mix(in oklab, var(--brand) 35%, transparent); }
.meta { color: var(--muted); font-size: .88rem; margin: .25rem 0; }
.note { font-size: .88rem; padding: .45rem .75rem; border-radius: var(--r-sm); background: var(--review-bg);
  color: var(--review); border: 1px solid var(--review-line); }

/* The proof itself: base and head across attempts. */
.evidence { width: 100%; border-collapse: separate; border-spacing: 6px; margin: 1.1rem -6px .4rem;
  font-variant-numeric: tabular-nums; }
.evidence th { font-size: .76rem; font-weight: 500; color: var(--faint); text-align: left; padding: .2rem .4rem; }
.evidence th[scope="row"] { color: var(--fg); font-size: .92rem; font-weight: 600; white-space: nowrap; }
.evidence th[scope="row"] code { font-size: .78em; margin-left: .2rem; }
.evidence td.expected { font-size: .84rem; color: var(--muted); padding: .35rem .4rem; vertical-align: middle; min-width: 12rem; }
.outcome { text-align: center; font-family: var(--mono); font-size: .8rem; font-weight: 600; letter-spacing: .06em;
  padding: .8rem .5rem; border-radius: var(--r-sm); width: 5.75rem; background: var(--raised); color: var(--muted);
  border: 1px solid var(--rule); animation: stamp .5s var(--ease) both; }
.evidence tr:nth-child(1) .outcome:nth-of-type(2) { animation-delay: .35s; }
.evidence tr:nth-child(1) .outcome:nth-of-type(3) { animation-delay: .42s; }
.evidence tr:nth-child(1) .outcome:nth-of-type(4) { animation-delay: .49s; }
.evidence tr:nth-child(2) .outcome:nth-of-type(2) { animation-delay: .56s; }
.evidence tr:nth-child(2) .outcome:nth-of-type(3) { animation-delay: .63s; }
.evidence tr:nth-child(2) .outcome:nth-of-type(4) { animation-delay: .7s; }
@keyframes stamp { from { opacity: 0; transform: scale(.9); } to { opacity: 1; transform: none; } }
.outcome.pass { background: var(--pass-bg); color: var(--pass); border-color: var(--pass-line); }
.outcome.fail { background: var(--block-bg); color: var(--block); border-color: var(--block-line); }
.outcome.error, .outcome.timeout { background: var(--review-bg); color: var(--review); border-color: var(--review-line); }

.classification { font-size: .92rem; color: var(--muted); }
.classification strong { font-family: var(--mono); font-size: .8rem; font-weight: 600; letter-spacing: .04em;
  padding: .15rem .5rem; border-radius: 999px; background: var(--raised); color: var(--fg); border: 1px solid var(--rule); }
.classification strong.regression { background: var(--block-bg); color: var(--block); border-color: var(--block-line); }

.claims { list-style: none; padding: 0; margin: 0; }
.claim { padding: 1rem 0; border-bottom: 1px solid var(--rule); }
.claim:first-child { padding-top: 0; }
.claim:last-child { border-bottom: 0; }
.claim.rejected { opacity: .8; }
.claim h3 { font-size: 1rem; margin: 0; }
.kind { font-family: var(--mono); font-size: .7rem; font-weight: 500; padding: .1rem .5rem; border-radius: 999px;
  border: 1px solid var(--rule); color: var(--muted); vertical-align: middle; }
.kind.change { color: var(--brand); border-color: color-mix(in oklab, var(--brand) 40%, transparent);
  background: color-mix(in oklab, var(--brand) 10%, transparent); }
.kind.contested { background: var(--review-bg); color: var(--review); border-color: var(--review-line); }

.receipt h2 { border-top: 0; margin-top: 2.25rem; padding-top: 0; }
.receipt ul, .scope ul, section > ul { padding-left: 1.2rem; }
.receipt li::marker, section > ul li::marker { color: var(--faint); }
.receipt li + li { margin-top: .3rem; }
.provenance { display: grid; grid-template-columns: max-content 1fr; gap: .55rem 1.5rem; margin: 0;
  padding: 1.1rem 1.25rem; background: var(--surface); border: 1px solid var(--rule); border-radius: var(--r-md); }
.provenance dt { color: var(--faint); font-size: .86rem; }
.provenance dd { margin: 0; overflow-wrap: anywhere; font-size: .92rem; }
.provenance dd.sha { color: var(--muted); }
/* Prose in the sections keeps a reading measure; cards stay full width for the evidence tables. */
section > p, section > ul { max-width: 72ch; }
@media (max-width: 40rem) {
  body { font-size: 15px; }
  main { padding-top: 1.25rem; }
  .banner { padding: 1.3rem 1.1rem 1.1rem; }
  .card { padding: 1.1rem 1rem; }
  /* Each revision becomes a block: label and expectation, then the attempts in one row. */
  .evidence, .evidence thead, .evidence tbody { display: block; }
  .evidence { margin: 1rem 0 .25rem; }
  .evidence tr { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-bottom: .6rem; }
  .evidence thead th:nth-child(-n+2) { display: none; }
  .evidence th[scope="row"], .evidence td.expected { grid-column: 1 / -1; padding: 0; }
  .evidence td.expected { min-width: 0; }
  .outcome { width: auto; padding: .6rem .25rem; }
  .provenance { grid-template-columns: 1fr; gap: 0; }
  .provenance dd { margin-bottom: .6rem; }
}
@media (prefers-reduced-motion: reduce) { .banner .decision, .outcome { animation: none; } }
@media print { .banner .decision, .outcome { animation: none; } }"""


_FONTS = Path(__file__).parent / "dashboard_static" / "legacy" / "vendor"


@cache
def _font_faces() -> str:
    """Geist, inlined as data URIs so the report stays one file with no external requests."""
    faces = []
    for family, name in (("Geist", "geist-latin-wght-normal.woff2"),
                         ("Geist Mono", "geist-mono-latin-wght-normal.woff2")):
        path = _FONTS / name
        if path.is_file():  # missing font: the system stack in _CSS takes over
            data = base64.b64encode(path.read_bytes()).decode()
            faces.append(f'@font-face {{ font-family: "{family}"; font-weight: 100 900; '
                         f'src: url(data:font/woff2;base64,{data}) format("woff2"); }}\n')
    return "".join(faces)


def _plural(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word if n == 1 else plural or word + 's'}"


def scope_receipt(proof: Proof) -> list[str]:
    """What this run actually checked, in plain sentences: the limits of its verdict."""
    ids = [h.id for h in proof.hypotheses.hypotheses]
    probes = {e.hypothesis_id: e for e in proof.experiments if e.hypothesis_id in ids and e.executions}
    attempts = max((r.attempt for e in probes.values() for r in e.executions), default=0)
    # A probe counts as evidence only if it passed on base every time (probe-validity rule).
    valid = [i for i, e in probes.items()
             if all(r.outcome == "PASS" for r in e.executions if r.revision == "base")]
    lines = [f"Spark proposed {_plural(len(ids), 'hypothesis', 'hypotheses')}."]
    # Tamper experiments (T1, T2…) are one run per changed test file whose base version exists.
    tamper_runs = [e for e in proof.experiments if e.hypothesis_id not in ids and e.executions]
    if probes:
        lines.append(f"{_plural(len(probes), 'probe')} ran on base and head, "
                     f"{_plural(attempts, 'attempt')} each; {len(valid)} passed on base every time.")
    elif tamper_runs:
        lines.append("No hypothesis probe ran; only the changed tests below were run.")
    else:
        lines.append("No probe ran, so nothing in this run tested the change directly.")
    unprobed = [i for i in ids if i not in probes]
    if unprobed:
        lines.append(f"No probe ran for {', '.join(unprobed)}.")
    n = len(tamper_runs)
    lines.append(f"{_plural(n, 'test file')} changed by the pull request "
                 f"{'was' if n == 1 else 'were'} run from base on head.")
    if proof.verdict.decision == "PASS":
        lines.append("PASS covers only these checks.")
    return lines


def _receipt(proof: Proof) -> str:
    items = "".join(f"<li>{html.escape(line)}</li>" for line in scope_receipt(proof))
    return f'<section class="receipt" aria-label="What this run checked"><h2>What this run checked</h2><ul>{items}</ul></section>'


def _banner(proof: Proof) -> str:
    e = html.escape
    reasons = "".join(f"<li>{e(reason)}</li>" for reason in proof.verdict.reasons)
    decision = proof.verdict.decision
    return f"""<header class="banner {decision.lower()}">
<p class="decision">{decision}</p>
<p class="headline">{e(_HEADLINE[decision])}</p>
<p class="run">run <code>{e(proof.run_id)}</code></p>
<ul>{reasons}</ul>
</header>"""


def _claims(proof: Proof) -> str:
    e = html.escape
    blocks = []
    for claim in proof.intent.claims:
        quote = (
            f"<blockquote>{e(claim.source_quote)}</blockquote>"
            if claim.source_quote is not None
            else '<p class="muted">implicit claim — not stated in the intent text</p>'
        )
        contested = (
            ' <span class="kind contested">contested by Muse Wonder</span>'
            if claim.id in proof.contested_claim_ids else ""
        )
        blocks.append(
            f'<li class="claim"><h3><code>{e(claim.id)}</code> '
            f'<span class="kind {e(claim.kind).lower()}">{e(claim.kind)}</span>{contested}</h3>'
            f"<p>{e(claim.statement)}</p>{quote}</li>"
        )
    for rejected in proof.intent.rejected:
        blocks.append(
            '<li class="claim rejected"><h3>rejected claim</h3>'
            f"<p>{e(rejected.statement)}</p><blockquote>{e(rejected.source_quote)}</blockquote>"
            '<p class="muted">quote not found in the intent text; kept as evidence only</p></li>'
        )
    body = "".join(blocks)
    return f'<section><h2>Intent claims</h2><ul class="claims">{body}</ul></section>'


def _tamper(proof: Proof) -> str:
    e = html.escape
    if not proof.tamper_findings:
        return "<section><h2>Test-tamper guard</h2><p>No tamper finding was recorded.</p></section>"
    items = "".join(
        f"<li><code>{e(f.test_path)}</code>"
        + (f" :: <code>{e(f.test_name)}</code>" if f.test_name is not None else "")
        + f" — {e(f.kind)}"
        + (
            f", base test on head: {e(f.base_test_on_head_outcome)}"
            if f.base_test_on_head_outcome is not None
            else ""
        )
        + "</li>"
        for f in proof.tamper_findings
    )
    return f"<section><h2>Test-tamper guard</h2><ul>{items}</ul></section>"


def _grid(experiment: Experiment | None, expected: dict[str, str]) -> str:
    e = html.escape
    if experiment is None or not experiment.executions:
        return (
            f"<p><strong>Expected on base:</strong> {e(expected['base'])}<br>"
            f"<strong>Expected on head:</strong> {e(expected['head'])}</p>"
            '<p class="muted">no probe was run for this hypothesis</p>'
        )
    attempts = sorted({run.attempt for run in experiment.executions})
    head = "".join(f"<th>attempt {attempt}</th>" for attempt in attempts)
    rows = []
    for revision in ("base", "head"):
        cells = []
        for attempt in attempts:
            run = next(
                (
                    r
                    for r in experiment.executions
                    if r.revision == revision and r.attempt == attempt
                ),
                None,
            )
            outcome = run.outcome if run is not None else "—"
            cells.append(f'<td class="outcome {e(outcome).lower()}">{e(outcome)}</td>')
        cells.insert(0, f'<td class="expected">{e(expected[revision])}</td>')
        sha = next((r.commit_sha for r in experiment.executions if r.revision == revision), "")
        label = f"{revision} <code>{e(sha[:7])}</code>" if sha else revision
        rows.append(f'<tr><th scope="row">{label}</th>{"".join(cells)}</tr>')
    return (
        f'<table class="evidence"><thead><tr><th></th><th>Expected</th>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _read_evidence(evidence_dir: Path, relative: str) -> str | None:
    root = evidence_dir.resolve()
    path = (evidence_dir / relative).resolve()
    # proof.json is untrusted: a path may not point outside the evidence directory.
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path.read_text(errors="replace")


def _probe_source(experiment: Experiment | None, evidence_dir: Path) -> str:
    source = (
        None if experiment is None else _read_evidence(evidence_dir, experiment.test_source_path)
    )
    if source is None:
        return '<p class="muted">probe source not captured</p>'
    return f"<pre>{html.escape(source)}</pre>"


def _head_failure(experiment: Experiment | None, evidence_dir: Path) -> str:
    """The pytest `E ` lines of the first failing head run: why head failed, in its own words."""
    if experiment is None:
        return ""
    run = next(
        (r for r in experiment.executions if r.revision == "head" and r.outcome == "FAIL"), None
    )
    log = None if run is None else _read_evidence(evidence_dir, run.stdout_path)
    lines = [] if log is None else [line for line in log.splitlines() if line.startswith("E ")]
    if not lines:
        return ""
    shown = html.escape("\n".join(lines[:MAX_FAILURE_LINES]))
    return f"<h4>Why head failed (attempt {run.attempt})</h4><pre>{shown}</pre>"


def _cited(claim: IntentClaim | None, claim_id: str) -> str:
    """The verified quote, not Spark's paraphrase: only the quote was checked against the intent."""
    e = html.escape
    if claim is None:
        return f'<p class="muted">cites unknown claim <code>{e(claim_id)}</code></p>'
    if claim.source_quote is None:
        text = '<p class="muted">implicit: behavior the intent does not describe</p>'
    else:
        text = f"<blockquote>{e(claim.source_quote)}</blockquote>"
    return f'<p class="meta">cites <code>{e(claim.id)}</code> {e(claim.kind)}</p>{text}'


def _reproducer(experiment: Experiment | None, finding: Finding | None) -> str:
    if finding is not None:
        command = finding.reproducer_command
    elif experiment is not None and experiment.executions:
        command = shlex.join(experiment.executions[0].command_argv)
    else:
        return ""
    return f"<h4>Reproduce</h4><pre><code>{html.escape(command)}</code></pre>"


def _cards(proof: Proof, evidence_dir: Path) -> str:
    e = html.escape
    experiments = {exp.hypothesis_id: exp for exp in proof.experiments}
    findings = {f.hypothesis_id: f for f in proof.findings}
    claims = {c.id: c for c in proof.intent.claims}
    cards = []
    for h in proof.hypotheses.hypotheses:
        experiment = experiments.get(h.id)
        finding = findings.get(h.id)
        claim = claims.get(h.claim_id)
        classification = "no finding recorded" if finding is None else finding.classification
        verified = " (verified)" if finding is not None and finding.verified else ""
        intended = " · possibly intended" if h.possibly_intended else ""
        mismatch = (
            '<p class="note">Muse Wonder: probe may not test this hypothesis</p>'
            if h.id in proof.mismatched_probe_ids else ""
        )
        explained = ""
        if h.id in proof.explained_probe_ids:
            change_id = proof.explained_probe_claim_ids.get(h.id, "")
            explained = f'<p class="note">Muse Wonder: may be the intended change {e(change_id)}</p>'
        cards.append(
            f'<section class="card"><h3><code>{e(h.id)}</code> {e(h.title)}</h3>'
            f"{_cited(claim, h.claim_id)}{mismatch}{explained}"
            f'<p class="meta">risk: {e(h.risk_category)}{intended}</p>'
            f"<p>{e(h.suspected_behavior)}</p>"
            f"{_grid(experiment, {'base': h.expected_base, 'head': h.expected_head})}"
            f'<p class="classification">classification: '
            f'<strong class="{e(classification).lower()}">{e(classification)}</strong>'
            f"{verified}</p>"
            f"{_head_failure(experiment, evidence_dir)}"
            f"<h4>Probe source</h4>{_probe_source(experiment, evidence_dir)}"
            f"{_reproducer(experiment, finding)}"
            "</section>"
        )
    body = "".join(cards) or '<p class="muted">No hypotheses were recorded for this run.</p>'
    return f"<section><h2>Hypotheses and probes</h2>{body}</section>"


def _models(usage: ModelUsage) -> str:
    e = html.escape
    if usage.endpoint is None:
        return ""
    names = ", ".join(f"{e(role)} {e(model or 'CLI default')}" for role, model in usage.models.items())
    warning = (
        '<dt>training</dt><dd class="note">contributor models were allowed:'
        " prompts, including repository code, may be used for training</dd>"
        if usage.contributor_allowed else ""
    )
    return f'<dt>models</dt><dd>{names} via <code>{e(usage.endpoint)}</code></dd>{warning}'


def _provenance(proof: Proof) -> str:
    e = html.escape
    probes = "".join(
        f"<dt>probe for <code>{e(exp.hypothesis_id)}</code></dt>"
        f'<dd class="sha">sha256 {e(exp.test_source_sha256)}</dd>'
        for exp in proof.experiments
    )
    usage = proof.model_usage
    language = (
        f'<dt>language</dt><dd>{e(proof.language)}</dd>'
        if proof.language != "python" else ""
    )
    return f"""<section><h2>Provenance</h2>
<dl class="provenance">
<dt>base</dt><dd class="sha">{e(proof.base_sha)}</dd>
<dt>head</dt><dd class="sha">{e(proof.head_sha)}</dd>
<dt>diff</dt><dd class="sha">sha256 {e(proof.diff_sha256)}</dd>
{language}
{probes}
<dt>model usage</dt><dd>{usage.calls} calls, {usage.prompt_tokens} prompt tokens,\
 {usage.completion_tokens} completion tokens</dd>
{_models(usage)}
</dl>
<p class="muted">Hashes detect that a file changed after capture; they are not signatures.</p>
</section>"""


def _scope(proof: Proof) -> str:
    e = html.escape
    limits = "".join(f"<li>{e(item)}</li>" for item in proof.verdict.scope_limitations)
    risks = "".join(f"<li>{e(item)}</li>" for item in proof.verdict.unresolved_risks)
    risks_html = f"<h3>Unresolved risks</h3><ul>{risks}</ul>" if risks else ""
    return f'<section class="scope"><h2>Scope</h2><ul>{limits}</ul>{risks_html}</section>'


def render_html(proof: Proof, evidence_dir: Path) -> str:
    """Render proof.json as one static HTML page; evidence_dir holds the recorded files."""
    e = html.escape
    body = (
        _banner(proof)
        + _receipt(proof)
        + _cards(proof, evidence_dir)
        + _tamper(proof)
        + _claims(proof)
        + _provenance(proof)
        + _scope(proof)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProofMerge {e(proof.run_id)} — {proof.verdict.decision}</title>
<style>{_font_faces()}{_CSS}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>"""
