import json
import re
import subprocess
import tempfile
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from proofmerge import __version__
from proofmerge.context import ContextError, get_context
from proofmerge.dashboard import make_server
from proofmerge.dashboard_events import JsonlEventSink
from proofmerge.diffbudget import view_diff
from proofmerge.evidence import Experiment, Proof, read_evidence
from proofmerge.hypotheses import MAX_HYPOTHESES, generate_hypotheses
from proofmerge.intent import extract_intent
from proofmerge.manifest import (
    ManifestError,
    evidence_sha256,
    verify_manifest,
    write_manifest,
)
from proofmerge.meta import SPARK_MODEL, MetaClient, MetaError
from proofmerge.pipeline import resolve_orchestrator, run_verify
from proofmerge.probes import MAX_BASE_RUNS, probe_hypotheses
from proofmerge.project_config import (
    ProjectConfig,
    PythonProject,
    assert_operator_config_allowed,
    load_operator_config,
    load_setup,
)
from proofmerge.rederive import rederive
from proofmerge.report import render_html, scope_receipt
from proofmerge.sandbox import (
    HYPOTHESIS_ID,
    RUNNER_IMAGE,
    SHA,
    SandboxError,
    image_digest,
    is_runner_failure,
    run_config_experiment,
    run_experiment,
    runner_dockerfile_path,
)
from proofmerge.telemetry import fan_out, verify_span

app = typer.Typer()

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help=(
            "Operator-controlled .proofmerge.toml. Replaces the base-commit file for "
            "this run. Not read from the pull request head. The GitHub Action does not "
            "pass this flag."
        ),
    ),
]


def _load_setup(
    repo: Path, base_sha: str, config_file: Path | None
) -> tuple[ProjectConfig | None, PythonProject | None, str]:
    if config_file is None:
        project, python = load_setup(repo, base_sha)
        return project, python, "the base commit's .proofmerge.toml"
    assert_operator_config_allowed(repo, base_sha, config_file)
    typer.echo(
        f"using operator config {config_file} "
        "(trusted because this process was given the path; not read from the pull request head)",
        err=True,
    )
    project, python = load_operator_config(config_file)
    return project, python, f"operator config {config_file}"


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
    """Make ONE real Spark-role call to check model access (Muse Spark by default)."""
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
    model = client.models[SPARK_MODEL] or "CLI default model"
    typer.echo(f"{model} via {client.endpoint}: ok={reply.ok} message={reply.message!r}")
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
def dashboard(
    folder: Annotated[Path, typer.Argument(help="Folder holding evidence runs (searched recursively)")],
    port: Annotated[int, typer.Option(help="Local port")] = 8765,
    repo: Annotated[
        Path | None, typer.Option(help="Git repo with a GitHub remote: list and verify its PRs")
    ] = None,
) -> None:
    """Serve a dashboard of every run under FOLDER on 127.0.0.1 (and, with --repo, its PRs)."""
    if not folder.is_dir():
        typer.echo(f"dashboard failed: {folder} is not a folder", err=True)
        raise typer.Exit(1)
    try:
        if repo is not None and not (repo / ".git").exists():
            raise OSError(f"{repo} is not a Git repository")
        server = make_server(folder, port, repo)
    except OSError as error:
        typer.echo(f"dashboard failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"dashboard: http://127.0.0.1:{server.server_port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


@app.command("app")
def open_app(
    folder: Annotated[
        Path | None, typer.Argument(help="Folder for evidence runs [default: ~/ProofMerge/runs]")
    ] = None,
    port: Annotated[int, typer.Option(help="Local port; a free one is used if it is busy")] = 8765,
    browser: Annotated[bool, typer.Option(help="Open the app in the web browser")] = True,
) -> None:
    """Open ProofMerge in the browser: verify changes, read runs and set up, no terminal needed."""
    folder = folder or Path.home() / "ProofMerge" / "runs"
    settings_file = Path.home() / "ProofMerge" / "settings.json"  # the model choice; never the key
    try:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            server = make_server(folder, port, settings_file=settings_file)
        except OSError:
            # the port is taken: let the system pick one
            server = make_server(folder, 0, settings_file=settings_file)
    except OSError as error:
        typer.echo(f"app failed: {error}", err=True)
        raise typer.Exit(1) from None
    url = f"http://127.0.0.1:{server.server_port}/app/"
    typer.echo(f"ProofMerge is open at {url}")
    typer.echo("Keep this window open while you use it. Close it, or press Quit in the app, to stop.")
    if browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def run_app() -> None:
    """Entry point for the `proofmerge-app` launcher (a double-clickable .exe on Windows)."""
    app(["app"])


@app.command()
def replay(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
    rerun: Annotated[bool, typer.Option(help="Re-run recorded experiments in Docker")] = False,
    repo: Annotated[Path | None, typer.Option(help="Git repository for --rerun")] = None,
    config_file: ConfigOption = None,
) -> None:
    """Check evidence hashes and optionally re-run recorded experiments in Docker."""
    evidence_dir = proof_json.parent
    problems = verify_manifest(evidence_dir)
    rederived: list[str] | None = None
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
        # Hashes are not signatures: also check the verdict follows from the evidence.
        rederived = rederive(proof)
        problems.extend(rederived or ())
    except (OSError, ValueError) as error:
        problems.append(f"Cannot read proof: {error}.")
    if problems:
        for problem in problems:
            typer.echo(problem)
        raise typer.Exit(1)
    manifest = json.loads((evidence_dir / "manifest.json").read_text())
    typer.echo(f"evidence intact: {len(manifest['files'])} files")
    if rederived is None:
        typer.echo("verdict not recomputed: this proof predates recorded verdict inputs")
    else:
        typer.echo(f"verdict recomputed from the evidence: {proof.verdict.decision}")
    if rerun:
        try:
            reproduced = _replay_experiments(proof, evidence_dir, repo, config_file)
        except (OSError, ValueError, SandboxError) as error:
            typer.echo(f"replay failed: {error}", err=True)
            raise typer.Exit(1) from None
        if not reproduced:
            raise typer.Exit(1)


def _checked_commits(proof: Proof, repo: Path) -> None:
    """Both recorded SHAs are full SHAs present in repo; never pass a ref or option to git."""
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


def _head_failures(proof: Proof, evidence_dir: Path) -> dict[str, str]:
    """The assertion lines of each probe's first failing head run, for the fix prompt."""
    failures = {}
    for experiment in proof.experiments:
        failed = next((e for e in experiment.executions if e.revision == "head" and e.outcome == "FAIL"), None)
        if failed is None:
            continue
        log = read_evidence(evidence_dir, failed.stdout_path)
        if log is None:
            continue
        lines = [x for x in log.splitlines() if x.startswith("E ")] or log.splitlines()[-30:]
        failures[experiment.hypothesis_id] = "\n".join(lines[:60])
    return failures


@app.command()
def fix(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
    repo: Annotated[Path, typer.Option(help="Git repository the run checked")],
    config_file: ConfigOption = None,
) -> None:
    """Suggest a fix for each verified regression and check it by re-running its probe in Docker.

    Writes <evidence dir>.fix/ beside the evidence. The verdict and the sealed evidence never change."""
    from proofmerge.fixes import FixError, fix_dir, regressions, suggest_fixes

    evidence_dir = proof_json.parent
    problems = verify_manifest(evidence_dir)
    if problems:
        typer.echo("The evidence changed since the run, so no fix is suggested:", err=True)
        for problem in problems:
            typer.echo(f"  {problem}", err=True)
        raise typer.Exit(1)
    try:
        proof = Proof.model_validate_json(proof_json.read_text())
        _checked_commits(proof, repo)
        if not regressions(proof):
            typer.echo("No verified regression in this run, so there is nothing to fix.")
            return
        config, python_project, _source = _load_setup(repo, proof.base_sha, config_file)
        context = get_context(repo, proof.base_sha, proof.head_sha)
        client = MetaClient(max_calls=len(regressions(proof)) * 2)
    except (OSError, ValueError, ContextError, MetaError) as error:
        typer.echo(f"fix failed: {error}", err=True)
        raise typer.Exit(1) from None
    experiments = {e.hypothesis_id: e for e in proof.experiments}

    def run_probe(sha: str, identifier: str, source: str, destination: Path) -> Experiment:
        experiment = experiments[identifier]
        attempts = max(1, sum(e.revision == "head" for e in experiment.executions))
        if config is None:
            options = {"attempts": attempts, "timeout": experiment.timeout_seconds}
            if python_project is not None:
                options["python_project"] = python_project
            return run_experiment(repo, proof.base_sha, sha, identifier, source, destination, **options)
        return run_config_experiment(repo, proof.base_sha, sha, identifier, source, config, destination,
                                     attempts=attempts, timeout=experiment.timeout_seconds)

    typer.echo(f"suggesting fixes for {', '.join(regressions(proof))} (each is checked in the sandbox)")
    try:
        report = suggest_fixes(proof, evidence_dir, repo, client, view_diff(context.diff_text).text,
                               context.changed_files, run_probe, _head_failures(proof, evidence_dir))
    except (OSError, ValueError, FixError, SandboxError) as error:
        typer.echo(f"fix failed: {error}", err=True)
        raise typer.Exit(1) from None
    for result in report.fixes:
        if result.verified:
            typer.echo(f"{result.hypothesis_id}: fix verified, probe base {result.base_outcomes}, "
                       f"fix {result.fixed_outcomes} ({', '.join(f.path for f in result.files)})")
        else:
            typer.echo(f"{result.hypothesis_id}: no verified fix: {result.problem}")
    typer.echo(f"written: {fix_dir(evidence_dir) / 'fix.json'}")
    _echo_usage(client)


@app.command()
def post(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
    pr: Annotated[int, typer.Option(help="Pull request number")],
    repo: Annotated[Path, typer.Option(help="Git repository the run checked")],
    mode: Annotated[str, typer.Option(help="inline: suggestions on the changed lines; overall: one comment")] = "inline",
    dry_run: Annotated[bool, typer.Option(help="Print the review instead of posting it")] = False,
) -> None:
    """Post the verdict and any suggested fixes to the pull request as one GitHub review comment."""
    from proofmerge.fixes import head_files, load_report
    from proofmerge.pr_review import ReviewError, build_review, post_review

    evidence_dir = proof_json.parent
    if verify_manifest(evidence_dir):
        typer.echo("The evidence changed since the run; check it with replay before posting.", err=True)
        raise typer.Exit(1)
    try:
        proof = Proof.model_validate_json(proof_json.read_text())
        _checked_commits(proof, repo)
        name = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                              cwd=repo, capture_output=True, text=True, timeout=30, check=False).stdout.strip()
        diff = subprocess.run(["gh", "pr", "diff", str(pr), "--repo", name], cwd=repo,
                              capture_output=True, text=True, timeout=60, check=False)
        if diff.returncode or not name:
            raise ReviewError("Could not read the pull request from GitHub. Check that gh is signed in.")
        report = load_report(evidence_dir)
        paths = [f.path for fx in (report.fixes if report else []) for f in fx.files]
        review = build_review(proof, report, diff.stdout, head_files(repo, proof.head_sha, paths), mode)
        if dry_run:
            typer.echo(json.dumps(review, indent=2))
            return
        url = post_review(name, pr, review)
    except (OSError, ValueError, ReviewError, subprocess.TimeoutExpired) as error:
        typer.echo(f"post failed: {error}", err=True)
        raise typer.Exit(1) from None
    inline = len(review.get("comments", []))
    typer.echo(f"posted to {name}#{pr}: {inline} inline suggestion(s){', ' if inline else ''}review {url}")


@app.command()
def approve(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
    pr: Annotated[int, typer.Option(help="Pull request number")],
    repo: Annotated[Path, typer.Option(help="Git repository the run checked")],
    dry_run: Annotated[bool, typer.Option(help="Print the review instead of posting it")] = False,
) -> None:
    """Approve the pull request on GitHub. Only a PASS run, on the commit it checked, can approve."""
    from proofmerge.pr_review import ReviewError, build_approval, post_review

    if verify_manifest(proof_json.parent):
        typer.echo("The evidence changed since the run; check it with replay before approving.", err=True)
        raise typer.Exit(1)
    try:
        proof = Proof.model_validate_json(proof_json.read_text())
        review = build_approval(proof)
        # Hashes alone do not show the recorded decision is the one the evidence gives: recompute it,
        # as replay does, so an edited PASS with a regenerated manifest cannot approve anything.
        problems = rederive(proof)
        if problems is None:
            raise ReviewError("This run predates recorded verdict inputs, so its verdict cannot be recomputed. "
                              "Verify the pull request again.")
        if problems:
            raise ReviewError("The recorded verdict does not follow from the evidence: " + "; ".join(problems[:3]))
        _checked_commits(proof, repo)
        name = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                              cwd=repo, capture_output=True, text=True, timeout=30, check=False).stdout.strip()
        view = subprocess.run(["gh", "pr", "view", str(pr), "--repo", name, "--json", "state,headRefOid,baseRefName"],
                              cwd=repo, capture_output=True, text=True, timeout=30, check=False)
        if view.returncode or not name:
            raise ReviewError("Could not read the pull request from GitHub. Check that gh is signed in.")
        current = json.loads(view.stdout)
        if current["state"] != "OPEN":
            raise ReviewError(f"PR #{pr} is {current['state'].lower()}, so there is nothing to approve.")
        if current["headRefOid"] != proof.head_sha:
            raise ReviewError(f"PR #{pr} changed since this run (head {current['headRefOid'][:7]}, "
                              f"checked {proof.head_sha[:7]}). Verify it again.")
        # A retargeted PR keeps its head but gets another diff and other required checks: the base this
        # run checked must still be part of the branch the PR now targets.
        target = current["baseRefName"]
        compare = subprocess.run(
            ["gh", "api", f"repos/{name}/compare/{proof.base_sha}...{target}", "--jq", ".status"],
            cwd=repo, capture_output=True, text=True, timeout=30, check=False)
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", target) or compare.stdout.strip() not in ("identical", "ahead"):
            raise ReviewError(f"PR #{pr} now targets {target}, which does not contain the base this run checked "
                              f"({proof.base_sha[:7]}). Verify it again.")
        if dry_run:
            typer.echo(json.dumps(review, indent=2))
            return
        url = post_review(name, pr, review)
    except (OSError, ValueError, KeyError, ReviewError, subprocess.TimeoutExpired) as error:
        typer.echo(f"approve failed: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"approved {name}#{pr}: review {url}")


@app.command()
def ponytail(
    proof_json: Annotated[Path, typer.Argument(help="Path to a proof.json file")],
    repo: Annotated[Path, typer.Option(help="Git repository the run checked")],
) -> None:
    """Review the run's diff for over-engineering: what to delete, what replaces it.

    Reads the diff only and never changes the verdict. Writes <evidence dir>.ponytail/ponytail.json."""
    from proofmerge.ponytail import ponytail_dir, review_diff

    try:
        proof = Proof.model_validate_json(proof_json.read_text())
        _checked_commits(proof, repo)
        context = get_context(repo, proof.base_sha, proof.head_sha)
        report = review_diff(proof, proof_json.parent, MetaClient(max_calls=1), context.diff_text)
    except (OSError, ValueError, ContextError, MetaError) as error:
        typer.echo(f"ponytail failed: {error}", err=True)
        raise typer.Exit(1) from None
    for finding in report.findings:
        typer.echo(f"{finding.location}: {finding.tag}: {finding.what} {finding.replacement}")
    typer.echo(f"net: -{report.net_lines} lines possible." if report.findings else "Lean already. Ship.")
    typer.echo(f"written: {ponytail_dir(proof_json.parent) / 'ponytail.json'}")


def _outcomes(experiment: Experiment, revision: str) -> str:
    return "".join(
        execution.outcome[0]
        for execution in sorted(experiment.executions, key=lambda execution: execution.attempt)
        if execution.revision == revision
    )


def _replay_experiments(
    proof: Proof, evidence_dir: Path, repo: Path | None, config_file: Path | None
) -> bool:
    if repo is None:
        raise ValueError("--repo is required with --rerun")
    _checked_commits(proof, repo)
    if not proof.experiments:
        raise ValueError("no recorded experiments to re-run")
    # Same rule as verify: the test setup comes from the recorded base commit, and a
    # config there means verify used the config runner, whatever its language.
    config, python_project, _source = _load_setup(repo, proof.base_sha, config_file)
    if (config.language if config else "python") != proof.language:
        raise ValueError(f"{repo} has no .proofmerge.toml for {proof.language} at the base commit")
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
        source = (evidence_dir / experiment.test_source_path).read_bytes().decode("utf-8")
        attempts = len(experiment.executions) // 2
        try:
            if config is None:
                options = {"attempts": attempts, "timeout": experiment.timeout_seconds}
                if python_project is not None:
                    options["python_project"] = python_project
                current = run_experiment(
                    repo, proof.base_sha, proof.head_sha, identifier, source, destination,
                    **options,
                )
            else:
                current = run_config_experiment(
                    repo, proof.base_sha, proof.head_sha, identifier, source, config,
                    destination, attempts=attempts, timeout=experiment.timeout_seconds,
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
    config_file: ConfigOption = None,
) -> None:
    """Print intent claims and Spark hypotheses for base..head (up to 2 real calls)."""
    try:
        context = get_context(repo, base, head)
        if not context.changed_files:
            typer.echo("no changes between base and head; nothing to check", err=True)
            return
        intent = intent_file.read_text() if intent_file else None
        client = MetaClient(max_calls=2)
        contract = extract_intent(intent, client)
        config, _python_project, _source = _load_setup(repo, context.base_sha, config_file)
        found = generate_hypotheses(context, contract, client, config=config)
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
    config_file: ConfigOption = None,
    orchestrator: Annotated[
        str | None,
        typer.Option(
            help="legacy (default) or langgraph; PROOFMERGE_ORCHESTRATOR sets the default. "
            "Both run the same stages and the same deterministic verdict policy."
        ),
    ] = None,
    events_file: Annotated[
        Path | None,
        typer.Option(
            hidden=True,
            help="Append stage progress as JSON lines here (the dashboard reads it). "
            "Must be outside the evidence directory; it is not evidence.",
        ),
    ] = None,
) -> None:
    """Verify base..head and write proof.json, report.html and a hashed manifest."""
    sink = None
    try:
        if events_file is not None and events_file.resolve().is_relative_to(
            evidence_dir.resolve()
        ):
            raise ValueError("--events-file must be outside the evidence directory")
        engine = resolve_orchestrator(orchestrator)
        context = get_context(repo, base, head)
        if not context.changed_files:
            typer.echo("no changes between base and head; nothing to check", err=True)
            return
        intent = intent_file.read_text() if intent_file else None
        budget = 4 + MAX_HYPOTHESES * MAX_BASE_RUNS
        try:
            client = MetaClient(max_calls=budget)
        except MetaError as error:
            client = MetaClient(completions=_UnavailableCompletions(error), max_calls=budget)
        extra: dict = {}
        if config_file is not None:
            # An operator-supplied config replaces the base commit's .proofmerge.toml.
            config, python_project, source = _load_setup(repo, context.base_sha, config_file)
            extra = {"config": config, "python_project": python_project,
                     "load_repo_config": False, "config_source": source}
        sink = JsonlEventSink(events_file) if events_file is not None else None
        with verify_span(evidence_dir.name, engine) as telemetry:
            observer = fan_out(sink, telemetry.observer if telemetry else None)
            if observer is not None:
                extra["observer"] = observer
            proof = run_verify(repo, base, head, intent, evidence_dir, client,
                               orchestrator=engine, **extra)
            if telemetry:
                telemetry.finish(proof.verdict.decision, client.calls, proof.language)
    except (ContextError, ValueError, OSError) as error:
        typer.echo(f"verify failed: {error}", err=True)
        raise typer.Exit(1) from None
    finally:
        if sink is not None:
            sink.close()
    typer.echo(f"verdict: {proof.verdict.decision}")
    for reason in proof.verdict.reasons:
        typer.echo(reason)
    typer.echo("checked: " + " ".join(scope_receipt(proof)))
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
    config_file: ConfigOption = None,
) -> None:
    """Write a Spark probe per hypothesis and run it on base and head in Docker."""
    try:
        context = get_context(repo, base, head)
        if not context.changed_files:
            typer.echo("no changes between base and head; nothing to check", err=True)
            return
        evidence_dir.mkdir(parents=True)  # fails on an existing dir, so old logs never mix in
        intent = intent_file.read_text() if intent_file else None
        client = MetaClient(max_calls=2 + MAX_HYPOTHESES * MAX_BASE_RUNS)
        contract = extract_intent(intent, client)
        config, python_project, _source = _load_setup(repo, context.base_sha, config_file)
        found = generate_hypotheses(context, contract, client, config=config)
        probes, experiments = probe_hypotheses(
            repo, context, contract, found.hypotheses, client, evidence_dir,
            config=config, python_project=python_project,
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
            typer.echo(f"{p.hypothesis_id}: no probe written (untestable with public names)")
        elif not p.passed_on_base:
            typer.echo(f"{p.hypothesis_id}: no probe passed on base after {p.drafts} drafts")
    _echo_usage(client)


if __name__ == "__main__":
    app()
