"""report.html: one self-contained evidence document rendered from proof.json.

Every string in proof.json comes from the PR and the model, so all of it is
escaped before it enters the page. The file is static: inline CSS, no scripts,
no external resources.
"""

import html
import shlex
from pathlib import Path

from proofmerge.evidence import Experiment, Finding, Proof
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
  --bg: #ffffff; --fg: #212b38; --muted: #55657a; --border: #d4dce5;
  --code-bg: #f2f5f8;
  --pass-bg: #e7f5ea; --pass-fg: #1e6e33;
  --review-bg: #fbf3dc; --review-fg: #6e4e06;
  --block-bg: #fbe9e8; --block-fg: #a12622;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #131c27; --fg: #e7edf4; --muted: #a3b2c4; --border: #2c3e52;
    --code-bg: #0f1823;
    --pass-bg: #12291b; --pass-fg: #5bc97a;
    --review-bg: #2e2510; --review-fg: #e3b341;
    --block-bg: #2e1614; --block-fg: #f47974;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 \
system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main, .banner { max-width: 60rem; }
main { margin: 0 auto; padding: 0 1rem 2rem; }
.banner { border-left: 6px solid; border-radius: 8px; padding: 1rem 1.25rem; margin: 1rem auto; }
.banner.pass { background: var(--pass-bg); color: var(--pass-fg); border-color: var(--pass-fg); }
.banner.review { background: var(--review-bg); color: var(--review-fg); border-color: var(--review-fg); }
.banner.block { background: var(--block-bg); color: var(--block-fg); border-color: var(--block-fg); }
.banner .decision { font-size: 1.6rem; font-weight: 700; letter-spacing: .05em; margin: 0; }
.banner .headline { font-weight: 600; margin: .25rem 0 0; }
.banner .run { margin: .25rem 0 0; font-size: .9rem; }
.banner ul { margin: .5rem 0 0; padding-left: 1.25rem; }
section { border: 1px solid var(--border); border-radius: 8px; padding: .75rem 1rem; margin: 1rem 0; }
.claim.rejected { border-style: dashed; }
h2 { font-size: 1.1rem; margin: 0 0 .5rem; }
h3, h4 { font-size: 1rem; margin: .25rem 0; }
blockquote { margin: .5rem 0 0; padding: .5rem .75rem; border-left: 3px solid var(--border); \
background: var(--code-bg); font-style: italic; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, \
"Liberation Mono", monospace; font-size: .9em; }
code { background: var(--code-bg); padding: 0 .25rem; border-radius: 4px; overflow-wrap: anywhere; }
pre { background: var(--code-bg); padding: .75rem; border-radius: 6px; overflow-x: auto; \
white-space: pre-wrap; word-break: break-word; }
table { border-collapse: collapse; margin: .5rem 0; }
th, td { border: 1px solid var(--border); padding: .25rem .75rem; text-align: center; }
.outcome.pass { color: var(--pass-fg); font-weight: 700; }
.outcome.fail { color: var(--block-fg); font-weight: 700; }
.outcome.error, .outcome.timeout { color: var(--review-fg); font-weight: 700; }
.muted { color: var(--muted); }
@media (max-width: 40rem) { body { font-size: 15px; } th, td { padding: .25rem .4rem; } }"""


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
        blocks.append(
            f'<section class="claim"><h3><code>{e(claim.id)}</code> · {e(claim.kind)}</h3>'
            f"<p>{e(claim.statement)}</p>{quote}</section>"
        )
    for rejected in proof.intent.rejected:
        blocks.append(
            '<section class="claim rejected"><h3>rejected claim</h3>'
            f"<p>{e(rejected.statement)}</p><blockquote>{e(rejected.source_quote)}</blockquote>"
            '<p class="muted">quote not found in the intent text; kept as evidence only</p></section>'
        )
    body = "".join(blocks)
    return f"<section><h2>Intent claims</h2>{body}</section>"


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


def _grid(experiment: Experiment | None) -> str:
    if experiment is None or not experiment.executions:
        return '<p class="muted">no probe was run for this hypothesis</p>'
    e = html.escape
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
        sha = next((r.commit_sha for r in experiment.executions if r.revision == revision), "")
        label = f"{revision} <code>{e(sha[:7])}</code>" if sha else revision
        rows.append(f'<tr><th scope="row">{label}</th>{"".join(cells)}</tr>')
    return f"<table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


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
    return f'<p class="muted">cites <code>{e(claim.id)}</code> · {e(claim.kind)}</p>{text}'


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
        cards.append(
            f'<section class="card"><h3><code>{e(h.id)}</code> {e(h.title)}</h3>'
            f"{_cited(claim, h.claim_id)}"
            f'<p class="muted">risk: {e(h.risk_category)}{intended}</p>'
            f"<p>{e(h.suspected_behavior)}</p>"
            f"<p><strong>Expected on base:</strong> {e(h.expected_base)}<br>"
            f"<strong>Expected on head:</strong> {e(h.expected_head)}</p>"
            f"{_grid(experiment)}"
            f"{_head_failure(experiment, evidence_dir)}"
            f"<p>classification: <strong>{e(classification)}</strong>{verified}</p>"
            f"<h4>Probe source</h4>{_probe_source(experiment, evidence_dir)}"
            f"{_reproducer(experiment, finding)}"
            "</section>"
        )
    body = "".join(cards)
    return f"<section><h2>Hypotheses and probes</h2>{body}</section>"


def _provenance(proof: Proof) -> str:
    e = html.escape
    probes = "".join(
        f"<li>probe for <code>{e(exp.hypothesis_id)}</code>: sha256 <code>{e(exp.test_source_sha256)}</code></li>"
        for exp in proof.experiments
    )
    usage = proof.model_usage
    return f"""<section><h2>Provenance</h2>
<ul>
<li>base <code>{e(proof.base_sha)}</code></li>
<li>head <code>{e(proof.head_sha)}</code></li>
<li>diff sha256 <code>{e(proof.diff_sha256)}</code></li>
{probes}
<li>model usage: {usage.calls} calls, {usage.prompt_tokens} prompt tokens,\
 {usage.completion_tokens} completion tokens</li>
</ul>
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
        + _claims(proof)
        + _tamper(proof)
        + _cards(proof, evidence_dir)
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
