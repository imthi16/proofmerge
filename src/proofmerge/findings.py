"""Normalize recorded probe executions into deterministic findings."""

import re

from proofmerge.evidence import Classification, Experiment, Finding
from proofmerge.hypotheses import Hypothesis
from proofmerge.probes import Probe
from proofmerge.sandbox import ATTEMPTS


def classify(experiment: Experiment) -> Classification:
    """Classify only ATTEMPTS agreeing PASS or FAIL runs on each revision."""
    base = [run.outcome for run in experiment.executions if run.revision == "base"]
    head = [run.outcome for run in experiment.executions if run.revision == "head"]
    if len(base) != ATTEMPTS or len(head) != ATTEMPTS:
        return "INCONCLUSIVE"
    passed, failed = ("PASS",) * ATTEMPTS, ("FAIL",) * ATTEMPTS
    pairs: dict[tuple[tuple[str, ...], tuple[str, ...]], Classification] = {
        (passed, failed): "REGRESSION",
        (failed, passed): "POSSIBLE_FIX",
        (failed, failed): "EXISTING_FAILURE",
        (passed, passed): "NO_DIFFERENCE",
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
