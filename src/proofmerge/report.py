"""report.html: one self-contained evidence document rendered from proof.json.

Every string in proof.json comes from the PR and the model, so all of it is
escaped before it enters the page. The file is static: inline CSS, no scripts,
no external resources.
"""

import html
import shlex
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
  color-scheme: light dark;
  --bg: #fbfcfd; --fg: #17202b; --muted: #586678; --rule: #dde3ea;
  --surface: #ffffff; --code-bg: #f1f4f7; --select: #cfe0f5;
  --pass-bg: #e3f3e7; --pass-fg: #1b6a31;
  --review-bg: #fbf1d6; --review-fg: #6b4a04;
  --block-bg: #fbe6e4; --block-fg: #a3221d;
  --change-fg: #2c5aa0; --change-bg: #e5edf9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10171f; --fg: #e6ecf3; --muted: #9fb0c3; --rule: #273746;
    --surface: #16202b; --code-bg: #0c131b; --select: #2b4a70;
    --pass-bg: #123020; --pass-fg: #6fd18c;
    --review-bg: #33280f; --review-fg: #e8bb4f;
    --block-bg: #3a1714; --block-fg: #ff8a83;
    --change-fg: #8fb4f0; --change-bg: #1a2a42;
  }
}
* { box-sizing: border-box; }
::selection { background: var(--select); color: var(--fg); }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 \
system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; \
-webkit-font-smoothing: antialiased; }
main { max-width: 60rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
p { margin: .5rem 0; }
code, pre, .sha { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \
"Liberation Mono", monospace; font-size: .88em; }
code { background: var(--code-bg); padding: .05rem .3rem; border-radius: 4px; overflow-wrap: anywhere; }
pre { background: var(--code-bg); padding: .85rem 1rem; border-radius: 8px; margin: .5rem 0 1rem; \
overflow-x: auto; white-space: pre-wrap; word-break: break-word; line-height: 1.5; }
pre code { background: none; padding: 0; font-size: 1em; }
.muted { color: var(--muted); }
h2 { font-size: 1.25rem; letter-spacing: -.01em; margin: 2.75rem 0 1rem; padding-top: 1rem; \
border-top: 1px solid var(--rule); }
h3 { font-size: 1.15rem; letter-spacing: -.01em; line-height: 1.3; margin: 0 0 .5rem; text-wrap: balance; }
h4 { font-size: .95rem; margin: 1.25rem 0 .25rem; }
blockquote { margin: .5rem 0; padding: .5rem .85rem; background: var(--code-bg); \
border-radius: 6px; font-style: italic; }

.banner { border-radius: 12px; padding: 1.5rem 1.5rem 1.25rem; border: 2px solid; }
.banner.pass { background: var(--pass-bg); color: var(--pass-fg); border-color: var(--pass-fg); }
.banner.review { background: var(--review-bg); color: var(--review-fg); border-color: var(--review-fg); }
.banner.block { background: var(--block-bg); color: var(--block-fg); border-color: var(--block-fg); }
.banner .decision { font-size: 2.75rem; font-weight: 800; letter-spacing: -.02em; line-height: 1; margin: 0; }
.banner .headline { font-size: 1.2rem; font-weight: 650; margin: .5rem 0 0; color: var(--fg); }
.banner .run { margin: .75rem 0 0; font-size: .9rem; color: var(--fg); }
.banner .run code { background: var(--surface); }
.banner ul { margin: .75rem 0 0; padding-left: 1.2rem; color: var(--fg); }
.banner li + li { margin-top: .25rem; }

.card { background: var(--surface); border: 1px solid var(--rule); border-radius: 12px; \
padding: 1.25rem 1.5rem 1rem; margin: 0 0 1.5rem; box-shadow: 0 1px 2px rgba(23, 32, 43, .05), \
0 4px 16px rgba(23, 32, 43, .04); }
.card h3 code { font-size: .8em; margin-right: .35rem; }
.meta { color: var(--muted); font-size: .92rem; margin: .25rem 0; }
.note { font-size: .92rem; padding: .4rem .75rem; border-radius: 6px; background: var(--review-bg); \
color: var(--review-fg); }

.evidence { width: 100%; border-collapse: separate; border-spacing: 4px; margin: 1rem -4px .25rem; \
font-variant-numeric: tabular-nums; }
.evidence th { font-size: .82rem; font-weight: 600; color: var(--muted); text-align: left; \
padding: .2rem .5rem; }
.evidence th[scope="row"] { color: var(--fg); font-size: .95rem; white-space: nowrap; }
.evidence td.expected { font-size: .88rem; color: var(--muted); padding: .35rem .5rem; \
vertical-align: middle; min-width: 12rem; }
.outcome { text-align: center; font-weight: 800; letter-spacing: .02em; padding: .7rem .5rem; \
border-radius: 6px; width: 5.5rem; background: var(--code-bg); }
.outcome.pass { background: var(--pass-bg); color: var(--pass-fg); }
.outcome.fail { background: var(--block-bg); color: var(--block-fg); }
.outcome.error, .outcome.timeout { background: var(--review-bg); color: var(--review-fg); }

.classification { font-size: .95rem; }
.classification strong { padding: .1rem .45rem; border-radius: 4px; background: var(--code-bg); }
.classification strong.regression { background: var(--block-bg); color: var(--block-fg); }

.claims { list-style: none; padding: 0; margin: 0; }
.claim { padding: .85rem 0; border-bottom: 1px solid var(--rule); }
.claim:first-child { padding-top: 0; }
.claim:last-child { border-bottom: 0; }
.claim.rejected { opacity: .85; }
.claim h3 { font-size: 1rem; margin: 0; }
.kind { font-size: .78rem; font-weight: 700; padding: .1rem .45rem; border-radius: 4px; \
background: var(--code-bg); color: var(--muted); }
.kind.change { background: var(--change-bg); color: var(--change-fg); }
.kind.contested { background: var(--review-bg); color: var(--review-fg); }

.receipt h2 { border-top: 0; margin-top: 1.5rem; padding-top: 0; }
.receipt ul { margin: 0; padding-left: 1.2rem; }
.receipt li + li { margin-top: .2rem; }
.provenance { display: grid; grid-template-columns: max-content 1fr; gap: .4rem 1.25rem; margin: 0; }
.provenance dt { color: var(--muted); font-size: .92rem; }
.provenance dd { margin: 0; overflow-wrap: anywhere; }
.scope ul, section > ul { padding-left: 1.2rem; }
@media (max-width: 40rem) {
  body { font-size: 15px; }
  .banner { padding: 1.1rem 1rem 1rem; }
  .banner .decision { font-size: 2.1rem; }
  .card { padding: 1rem; }
  /* Each revision becomes a block: label and expectation, then the attempts in one row. */
  .evidence, .evidence thead, .evidence tbody { display: block; }
  .evidence { margin: 1rem 0 .25rem; }
  .evidence tr { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; margin-bottom: .5rem; }
  .evidence thead th:nth-child(-n+2) { display: none; }
  .evidence th[scope="row"], .evidence td.expected { grid-column: 1 / -1; padding: 0; }
  .evidence td.expected { min-width: 0; }
  .outcome { width: auto; padding: .55rem .25rem; }
  .provenance { grid-template-columns: 1fr; gap: 0; }
  .provenance dd { margin-bottom: .5rem; }
}"""


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
<style>{_CSS}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>"""
