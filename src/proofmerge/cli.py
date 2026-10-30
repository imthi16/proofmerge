import json
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from proofmerge import __version__
from proofmerge.context import ContextError, get_context
from proofmerge.evidence import Experiment, Proof
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
from proofmerge.sandbox import (
    HYPOTHESIS_ID,
    RUNNER_IMAGE,
    SHA,
    SandboxError,
    image_digest,
    is_runner_failure,
    run_experiment,
    runner_dockerfile_path,
)

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
    rerun: Annotated[bool, typer.Option(help="Re-run recorded experiments in Docker")] = False,
    repo: Annotated[Path | None, typer.Option(help="Git repository for --rerun")] = None,
) -> None:
    """Check evidence hashes and optionally re-run recorded experiments in Docker."""
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
    if rerun:
        try:
            reproduced = _replay_experiments(proof, evidence_dir, repo)
        except (OSError, ValueError, SandboxError) as error:
            typer.echo(f"replay failed: {error}", err=True)
            raise typer.Exit(1) from None
        if not reproduced:
            raise typer.Exit(1)


def _outcomes(experiment: Experiment, revision: str) -> str:
    return "".join(
        execution.outcome[0]
        for execution in sorted(experiment.executions, key=lambda execution: execution.attempt)
        if execution.revision == revision
    )


def _replay_experiments(proof: Proof, evidence_dir: Path, repo: Path | None) -> bool:
    if repo is None:
        raise ValueError("--repo is required with --rerun")
    # Validate both before either reaches git; a ref or an option must never be executed.
    for sha in (proof.base_sha, proof.head_sha):
        if not SHA.fullmatch(sha):
            raise ValueError(f"not a full commit SHA: {sha!r}")
    for sha in (proof.base_sha, proof.head_sha):
        result = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True, check=False,
        )
        if result.returncode:
            raise ValueError(f"repository {repo} lacks recorded commit {sha} (or is not readable)")
    if not proof.experiments:
        raise ValueError("no recorded experiments to re-run")
    seen = set()
    for experiment in proof.experiments:
        identifier = experiment.hypothesis_id
        if not HYPOTHESIS_ID.fullmatch(identifier) or identifier in seen:
            raise ValueError(f"invalid or duplicate experiment id: {identifier!r}")
        seen.add(identifier)
        counts = []
        for revision, sha in (("base", proof.base_sha), ("head", proof.head_sha)):
            executions = [e for e in experiment.executions if e.revision == revision]
            count = len(executions)
            if (
                not count
                or sorted(e.attempt for e in executions) != list(range(1, count + 1))
                or any(e.commit_sha != sha for e in executions)
            ):
                raise ValueError(f"{identifier}: invalid recorded {revision} executions")
            counts.append(count)
        if counts[0] != counts[1] or experiment.timeout_seconds <= 0:
            raise ValueError(f"{identifier}: unequal attempt counts or invalid timeout")

    # Keep the directory for inspection after exit, including on a runner failure.
    destination = Path(tempfile.mkdtemp(prefix="proofmerge-replay-"))
    typer.echo(f"re-run evidence: {destination}")
    reproduced = True
    for experiment in proof.experiments:
        identifier = experiment.hypothesis_id
        recorded = f"base {_outcomes(experiment, 'base')} head {_outcomes(experiment, 'head')}"
        try:
            current = run_experiment(
                repo, proof.base_sha, proof.head_sha, identifier,
                (evidence_dir / experiment.test_source_path).read_bytes().decode("utf-8"),
                destination, attempts=len(experiment.executions) // 2,
                timeout=experiment.timeout_seconds,
            )
        except (OSError, ValueError, SandboxError) as error:
            typer.echo(f"{identifier} recorded {recorded}: NOT reproduced ({error})")
            reproduced = False
            continue
        if any(is_runner_failure(execution.exit_code) for execution in current.executions):
            typer.echo(f"{identifier} recorded {recorded}: NOT reproduced (runner failure)")
            reproduced = False
            continue
        now = f"base {_outcomes(current, 'base')} head {_outcomes(current, 'head')}"
        if recorded == now:
            typer.echo(f"{identifier} {recorded}: reproduced")
        else:
            typer.echo(f"{identifier} recorded {recorded}, now {now}: NOT reproduced")
            reproduced = False
    return reproduced


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
        budget = 4 + MAX_HYPOTHESES * MAX_BASE_RUNS
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
