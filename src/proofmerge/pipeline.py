"""Wire verification stages into a recorded, replayable evidence bundle."""

from pathlib import Path

from proofmerge import __version__
from proofmerge.context import ContextError, get_context
from proofmerge.evidence import ModelUsage, Proof
from proofmerge.findings import findings_for
from proofmerge.hypotheses import HypothesisSet, check_diff, generate_hypotheses
from proofmerge.intent import C0, IntentContract, extract_intent
from proofmerge.manifest import write_manifest
from proofmerge.meta import MetaClient, MetaError
from proofmerge.probes import probe_hypotheses
from proofmerge.report import render_html
from proofmerge.sandbox import Runner, SandboxError, docker_runner
from proofmerge.tamper import TamperError, find_tamper, run_base_tests_on_head
from proofmerge.verdict import decide


def run_verify(
    repo: Path,
    base: str,
    head: str,
    intent: str | None,
    evidence_dir: Path,
    client: MetaClient,
    *,
    runner: Runner = docker_runner,
    digest: str | None = None,
) -> Proof:
    """Resolve context, collect evidence, adjudicate, then seal the bundle last."""
    context = get_context(repo, base, head)
    check_diff(context)
    evidence_dir.mkdir(parents=True)
    errors: list[str] = []
    contract = IntentContract(claims=[C0], rejected=[])
    found = HypothesisSet(hypotheses=[], rejected=[])
    probes, experiments = [], []
    try:
        contract = extract_intent(intent, client)
        found = generate_hypotheses(context, contract, client)
        probes, experiments = probe_hypotheses(
            repo, context, contract, found.hypotheses, client, evidence_dir,
            runner=runner, digest=digest,
        )
    except (MetaError, SandboxError, OSError, ValueError) as error:
        errors.append(str(error))

    tamper, tamper_experiments = [], []
    try:
        tamper = find_tamper(repo, context)
        tamper, tamper_experiments = run_base_tests_on_head(
            repo, context, tamper, evidence_dir, runner=runner, digest=digest
        )
    except (TamperError, SandboxError, ContextError, OSError, ValueError) as error:
        errors.append(str(error))

    for experiment in experiments + tamper_experiments:
        for execution in experiment.executions:
            if execution.outcome in {"ERROR", "TIMEOUT"}:
                errors.append(
                    f"{experiment.hypothesis_id} {execution.revision} attempt "
                    f"{execution.attempt}: {execution.outcome}"
                )
    for finding in tamper:
        if finding.base_test_on_head_outcome in {"ERROR", "TIMEOUT"}:
            errors.append(f"{finding.test_path}: {finding.base_test_on_head_outcome}")

    findings = findings_for(
        experiments, found.hypotheses, probes, proof_path=str(evidence_dir / "proof.json")
    )
    verdict = decide(
        contract, found.hypotheses, findings, tamper, errors=errors, review_on_error=True
    )
    usage = [u for u in client.usage if u is not None]
    proof = Proof(
        proofmerge_version=__version__,
        run_id=evidence_dir.name,
        base_sha=context.base_sha,
        head_sha=context.head_sha,
        diff_sha256=context.diff_sha256,
        intent=contract,
        hypotheses=found,
        experiments=experiments + tamper_experiments,
        findings=findings,
        tamper_findings=tamper,
        verdict=verdict,
        model_usage=ModelUsage(
            calls=client.calls,
            prompt_tokens=sum(u.prompt_tokens for u in usage),
            completion_tokens=sum(u.completion_tokens for u in usage),
        ),
    )
    (evidence_dir / "proof.json").write_text(proof.model_dump_json(indent=2), encoding="utf-8")
    (evidence_dir / "report.html").write_text(render_html(proof, evidence_dir), encoding="utf-8")
    write_manifest(evidence_dir)
    return proof
