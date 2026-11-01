"""Recompute a proof's findings and verdict from its recorded evidence.

Hashes show the files did not change after the run; they are not signatures, so
someone who edits proof.json can also rewrite the manifest. This check reclassifies
every recorded execution and runs the same verdict policy again, so a proof whose
verdict does not follow from its own records fails replay.

Limits: it shows the verdict follows from what the proof records. It cannot show the
records are true. Someone who rewrites executions, logs, stage errors and the verdict
together, consistently, passes it. `replay --rerun` re-executes the probes instead.
"""

from proofmerge.evidence import Finding, Proof
from proofmerge.findings import findings_for
from proofmerge.probes import Probe
from proofmerge.sandbox import outcome_for
from proofmerge.verdict import adjudicate


def _shape(finding: Finding) -> tuple:
    # The reproducer command names the absolute evidence path, which moves with the bundle.
    return (finding.hypothesis_id, finding.claim_id, finding.classification, finding.verified)


def rederive(proof: Proof) -> list[str] | None:
    """Problems found by recomputing; [] when everything follows; None when not possible.

    None means the proof predates recorded verdict inputs, so only hashes can be checked.
    """
    if proof.verdict_inputs is None:
        return None
    # verify runs a probe on head only after it passed on base, so every recorded
    # hypothesis experiment stands for a base-passing probe.
    probes = [
        Probe(hypothesis_id=e.hypothesis_id, test_source="", drafts=0, passed_on_base=True)
        for e in proof.experiments
        if e.hypothesis_id.startswith("H")
    ]
    hypotheses = proof.hypotheses.hypotheses
    try:
        findings = findings_for(proof.experiments, hypotheses, probes, proof_path="")
        verdict = adjudicate(
            proof.intent, hypotheses, findings, proof.tamper_findings, proof.verdict_inputs,
            contested_claim_ids=frozenset(proof.contested_claim_ids),
            mismatched_probe_ids=frozenset(proof.mismatched_probe_ids),
            explained_probe_ids=frozenset(proof.explained_probe_ids),
            explained_probe_claim_ids=dict(proof.explained_probe_claim_ids),
        )
    except ValueError as error:
        return [f"Cannot recompute the verdict: {error}."]
    problems = []
    if proof.language == "python":
        # pytest outcomes are a function of the exit code alone (sandbox.outcome_for).
        for experiment in proof.experiments:
            for run in experiment.executions:
                if run.outcome != outcome_for(run.exit_code):
                    problems.append(
                        f"{experiment.hypothesis_id} {run.revision} attempt {run.attempt}: "
                        f"outcome {run.outcome} does not match exit code {run.exit_code}."
                    )
    recorded = sorted(map(_shape, proof.findings))
    recomputed = sorted(map(_shape, findings))
    if recorded != recomputed:
        problems.append(
            "Findings do not follow from the recorded executions: "
            f"recorded {_summary(recorded)}, recomputed {_summary(recomputed)}."
        )
    if verdict != proof.verdict:
        if verdict.decision != proof.verdict.decision:
            problems.append(
                f"Verdict does not follow from the evidence: recorded {proof.verdict.decision}, "
                f"recomputed {verdict.decision}."
            )
        else:
            problems.append(
                f"Verdict {verdict.decision} matches, but its recorded reasons or limits differ "
                "from the recomputed ones."
            )
    return problems


def _summary(shapes: list[tuple]) -> str:
    if not shapes:
        return "none"
    return ", ".join(
        f"{hid} {kind}{' verified' if verified else ''}" for hid, _, kind, verified in shapes
    )
