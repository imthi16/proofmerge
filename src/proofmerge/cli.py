import json
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from proofmerge import __version__
from proofmerge.context import ContextError, get_context
from proofmerge.evidence import Proof
from proofmerge.hypotheses import MAX_HYPOTHESES, check_diff, generate_hypotheses
from proofmerge.intent import extract_intent
from proofmerge.manifest import (
    ManifestError,
    evidence_sha256,
    verify_manifest,
    write_manifest,
)
from proofmerge.meta import SPARK_MODEL, MetaClient, MetaError
from proofmerge.pipeline import run_verify
from proofmerge.probes import MAX_BASE_RUNS, probe_hypotheses
from proofmerge.report import render_html
from proofmerge.sandbox import RUNNER_IMAGE, SandboxError, image_digest, runner_dockerfile_path

app = typer.Typer()


class SmokeReply(BaseModel):
    ok: bool
    message: str


@app.callback()
def main() -> None:
    """ProofMerge command-line interface."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"proofmerge {__version__}")


@app.command()
def setup() -> None:
    """Build the Docker runner image shipped with the package."""
    try:
        dockerfile = runner_dockerfile_path()
        context_dir = dockerfile.parent
    except SandboxError as error:
        typer.echo(f"setup failed: {error}", err=True)
        raise typer.Exit(1) from None
    try:
        result = subprocess.run(
            ["docker", "build", "-t", RUNNER_IMAGE, str(context_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        typer.echo("setup failed: docker is not installed or not on PATH", err=True)
        raise typer.Exit(1) from None
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "docker build failed"
        typer.echo(f"setup failed: {message}", err=True)
        raise typer.Exit(1) from None
    try:
        digest = image_digest()
    except SandboxError as error:
        typer.echo(f"setup failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"built runner image: {digest}")


@app.command()
def smoke() -> None:
    """Make ONE real Muse Spark call to check API access (uses MODEL_API_KEY)."""
    try:
        client = MetaClient(max_calls=1)
        reply = client.structured(
            SPARK_MODEL,
            developer="You are a connectivity check. Reply only with JSON matching the schema.",
            user='Set ok to true and message to "proofmerge smoke test".',
            schema=SmokeReply,
        )
    except MetaError as error:
        typer.echo(f"smoke failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{SPARK_MODEL}: ok={reply.ok} message={reply.message!r}")
    usage = client.usage[-1]
    if usage is not None:
        typer.echo(f"tokens: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")


@app.command()
def report(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
    out: Annotated[Path, typer.Option(help="Output HTML file")] = Path("report.html"),
) -> None:
    """Render proof.json as one self-contained report.html (evidence dir = its folder)."""
    try:
        proof = Proof.model_validate_json(proof_json.read_text())
        out.write_text(render_html(proof, proof_json.parent))
    except (OSError, ValueError) as error:
        typer.echo(f"report failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"wrote {out}")


@app.command()
def replay(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
) -> None:
    """Check recorded evidence and probe hashes without re-running experiments."""
    evidence_dir = proof_json.parent
    problems = verify_manifest(evidence_dir)
    try:
        evidence_sha256(evidence_dir, proof_json.name)
        proof = Proof.model_validate_json(proof_json.read_text())
        for experiment in proof.experiments:
            try:
                actual = evidence_sha256(evidence_dir, experiment.test_source_path)
                if actual != experiment.test_source_sha256:
                    problems.append(f"Probe hash mismatch: {experiment.test_source_path}.")
            except ManifestError as error:
                problems.append(str(error))
    except (OSError, ValueError) as error:
        problems.append(f"Cannot read proof: {error}.")
    if problems:
        for problem in problems:
            typer.echo(problem)
        raise typer.Exit(1)
    manifest = json.loads((evidence_dir / "manifest.json").read_text())
    typer.echo(f"evidence intact: {len(manifest['files'])} files")


@app.command()
def hypotheses(
    repo: Path,
    base: Annotated[str, typer.Option(help="Base ref, e.g. main")],
    head: Annotated[str, typer.Option(help="Head ref, e.g. candidate/tenant-cache-regression")],
    intent_file: Annotated[
        Path | None, typer.Option(help="File holding the PR description")
    ] = None,
) -> None:
    """Print intent claims and Spark hypotheses for base..head (up to 2 real calls)."""
    try:
        context = get_context(repo, base, head)
        check_diff(context)  # before any model call, so a refused diff costs nothing
        if not context.changed_files:
            typer.echo("no changes between base and head; nothing to check", err=True)
            return
        intent = intent_file.read_text() if intent_file else None
        client = MetaClient(max_calls=2)
        contract = extract_intent(intent, client)
        found = generate_hypotheses(context, contract, client)
    except (ContextError, MetaError, ValueError, OSError) as error:
        typer.echo(f"hypotheses failed: {error}", err=True)
        raise typer.Exit(1) from None
    report = {
        "base_sha": context.base_sha,
        "head_sha": context.head_sha,
        "diff_sha256": context.diff_sha256,
        "intent": contract.model_dump(),
        "hypotheses": found.model_dump(),
    }
    typer.echo(json.dumps(report, indent=2))
    _echo_usage(client)


def _echo_usage(client: MetaClient) -> None:
    usage = [u for u in client.usage if u is not None]
    prompt = sum(u.prompt_tokens for u in usage)
    completion = sum(u.completion_tokens for u in usage)
    typer.echo(f"calls={client.calls} tokens: prompt={prompt} completion={completion}", err=True)


class _UnavailableCompletions:
    """Defer client setup failure to the recorded model stage of verify."""

    def __init__(self, error: MetaError) -> None:
        self.error = str(error)

    def create(self, **kwargs):
        raise MetaError(self.error)


@app.command()
def verify(
    repo: Path,
    base: Annotated[str, typer.Option(help="Base ref, e.g. main")],
    head: Annotated[str, typer.Option(help="Head ref to verify")],
    evidence_dir: Annotated[Path, typer.Option(help="New directory for proof and report")],
    intent_file: Annotated[
        Path | None, typer.Option(help="File holding the PR description")
    ] = None,
) -> None:
    """Verify base..head and write proof.json, report.html and a hashed manifest."""
    try:
        context = get_context(repo, base, head)
        check_diff(context)
        if not context.changed_files:
            typer.echo("no changes between base and head; nothing to check", err=True)
            return
        intent = intent_file.read_text() if intent_file else None
        budget = 2 + MAX_HYPOTHESES * MAX_BASE_RUNS
        try:
            client = MetaClient(max_calls=budget)
        except MetaError as error:
            client = MetaClient(completions=_UnavailableCompletions(error), max_calls=budget)
        proof = run_verify(repo, base, head, intent, evidence_dir, client)
    except (ContextError, ValueError, OSError) as error:
        typer.echo(f"verify failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"verdict: {proof.verdict.decision}")
    for reason in proof.verdict.reasons:
        typer.echo(reason)
    typer.echo(f"report: {evidence_dir / 'report.html'}")
    _echo_usage(client)
    if proof.verdict.decision == "BLOCK":
        raise typer.Exit(2)


@app.command()
def probe(
    repo: Path,
    base: Annotated[str, typer.Option(help="Base ref, e.g. main")],
    head: Annotated[str, typer.Option(help="Head ref, e.g. candidate/tenant-cache-regression")],
    evidence_dir: Annotated[Path, typer.Option(help="New directory for probes and logs")],
    intent_file: Annotated[
        Path | None, typer.Option(help="File holding the PR description")
    ] = None,
) -> None:
    """Write a Spark probe per hypothesis and run it on base and head in Docker."""
    try:
        context = get_context(repo, base, head)
        check_diff(context)
        if not context.changed_files:
            typer.echo("no changes between base and head; nothing to check", err=True)
            return
        evidence_dir.mkdir(parents=True)  # fails on an existing dir, so old logs never mix in
        intent = intent_file.read_text() if intent_file else None
        client = MetaClient(max_calls=2 + MAX_HYPOTHESES * MAX_BASE_RUNS)
        contract = extract_intent(intent, client)
        found = generate_hypotheses(context, contract, client)
        probes, experiments = probe_hypotheses(
            repo, context, contract, found.hypotheses, client, evidence_dir
        )
    except (ContextError, MetaError, SandboxError, ValueError, OSError) as error:
        typer.echo(f"probe failed: {error}", err=True)
        raise typer.Exit(1) from None
    report = {
        "base_sha": context.base_sha,
        "head_sha": context.head_sha,
        "intent": contract.model_dump(),
        "hypotheses": found.model_dump(),
        "probes": [p.model_dump() for p in probes],
        "experiments": [e.model_dump() for e in experiments],
    }
    (evidence_dir / "probe-run.json").write_text(json.dumps(report, indent=2))
    try:
        write_manifest(evidence_dir)
    except (ManifestError, OSError) as error:
        typer.echo(f"probe failed: {error}", err=True)
        raise typer.Exit(1) from None
    for experiment in experiments:
        grid = {
            revision: "".join(x.outcome[0] for x in experiment.executions if x.revision == revision)
            for revision in ("base", "head")
        }
        typer.echo(f"{experiment.hypothesis_id}: base {grid['base']}  head {grid['head']}")
    for p in probes:
        if not p.test_source:
            typer.echo(f"{p.hypothesis_id}: Spark wrote no probe (untestable with public names)")
        elif not p.passed_on_base:
            typer.echo(f"{p.hypothesis_id}: no probe passed on base after {p.drafts} drafts")
    _echo_usage(client)
