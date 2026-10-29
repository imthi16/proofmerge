"""Normalize recorded probe executions into deterministic findings."""

import re

from proofmerge.evidence import Classification, Experiment, Finding
from proofmerge.hypotheses import Hypothesis
from proofmerge.probes import Probe


def classify(experiment: Experiment) -> Classification:
    """Classify only three agreeing PASS or FAIL runs on each revision."""
    base = [run.outcome for run in experiment.executions if run.revision == "base"]
    head = [run.outcome for run in experiment.executions if run.revision == "head"]
    if len(base) != 3 or len(head) != 3:
        return "INCONCLUSIVE"
    pairs: dict[tuple[tuple[str, ...], tuple[str, ...]], Classification] = {
        (("PASS",) * 3, ("FAIL",) * 3): "REGRESSION",
        (("FAIL",) * 3, ("PASS",) * 3): "POSSIBLE_FIX",
        (("FAIL",) * 3, ("FAIL",) * 3): "EXISTING_FAILURE",
        (("PASS",) * 3, ("PASS",) * 3): "NO_DIFFERENCE",
    }
    return pairs.get((tuple(base), tuple(head)), "INCONCLUSIVE")


def findings_for(
    experiments: list[Experiment],
    hypotheses: list[Hypothesis],
    probes: list[Probe],
    proof_path: str,
) -> list[Finding]:
    """Tie each hypothesis experiment to its claim and probe-validity evidence."""
    claims = {hypothesis.id: hypothesis.claim_id for hypothesis in hypotheses}
    passed = {probe.hypothesis_id for probe in probes if probe.passed_on_base}
    findings: list[Finding] = []
    for experiment in experiments:
        hypothesis_id = experiment.hypothesis_id
        if not hypothesis_id.startswith("H"):
            continue
        if hypothesis_id not in claims:
            raise ValueError(f"unknown hypothesis id: {hypothesis_id!r}")
        classification = classify(experiment)
        verified = (
            classification == "REGRESSION"
            and hypothesis_id in passed
            and re.fullmatch(r"[0-9a-fA-F]{64}", experiment.test_source_sha256) is not None
        )
        # shortcut: replay checks hashes only; re-running in Docker is a later task.
        findings.append(
            Finding(
                hypothesis_id=hypothesis_id,
                claim_id=claims[hypothesis_id],
                classification=classification,
                verified=verified,
                reproducer_command=f"proofmerge replay {proof_path}",
            )
        )
    return findings
