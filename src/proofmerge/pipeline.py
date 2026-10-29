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
from proofmerge.wonder import check_probes, cross_check_claims


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
    intent_established = False
    try:
        contract = extract_intent(intent, client)
        intent_established = True
    except (MetaError, OSError, ValueError) as error:
        errors.append(str(error))
    if intent_established:
        try:
            found = generate_hypotheses(context, contract, client)
            probes, experiments = probe_hypotheses(
                repo, context, contract, found.hypotheses, client, evidence_dir,
                runner=runner, digest=digest,
            )
        except (MetaError, SandboxError, OSError, ValueError) as error:
            done = getattr(error, "completed", None)
            if isinstance(done, dict):
                probes = done.get("probes", probes)
                experiments = done.get("experiments", experiments)
            errors.append(str(error))

    contested_claim_ids, mismatched_probe_ids = frozenset(), frozenset()
    try:
        contested_claim_ids = cross_check_claims(contract, intent, client)
    except MetaError as error:
        errors.append(str(error))
    try:
        mismatched_probe_ids = check_probes(found.hypotheses, probes, client)
    except MetaError as error:
        errors.append(str(error))

    tamper, tamper_experiments = [], []
    try:
        tamper = find_tamper(repo, context)
        tamper, tamper_experiments = run_base_tests_on_head(
            repo, context, tamper, evidence_dir, runner=runner, digest=digest
        )
    except (TamperError, SandboxError, ContextError, OSError, ValueError) as error:
        done = getattr(error, "completed", None)
        if isinstance(done, dict):
            tamper = done.get("tamper", tamper)
            tamper_experiments = done.get("experiments", tamper_experiments)
        errors.append(str(error))

    findings = findings_for(
        experiments, found.hypotheses, probes, proof_path=str(evidence_dir / "proof.json")
    )
    # Stage errors only add REVIEW reasons: hard evidence already gathered can still BLOCK.
    verdict = decide(
        contract, found.hypotheses, findings, tamper,
        contested_claim_ids=contested_claim_ids, mismatched_probe_ids=mismatched_probe_ids,
        errors=errors, intent_established=intent_established,
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
        contested_claim_ids=sorted(contested_claim_ids),
        mismatched_probe_ids=sorted(mismatched_probe_ids),
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
