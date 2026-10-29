"""Deterministic verdict policy over normalized evidence and intent claims."""

from proofmerge.evidence import Finding, TamperFinding, Verdict
from proofmerge.hypotheses import Hypothesis
from proofmerge.intent import C0, IntentContract


def decide(
    contract: IntentContract,
    hypotheses: list[Hypothesis],
    findings: list[Finding],
    tamper: list[TamperFinding],
    contested_claim_ids: frozenset[str] = frozenset(),
    errors: list[str] | None = None,
) -> Verdict:
    """Apply rules 1–9, preserving all reasons when BLOCK takes precedence."""
    claims = {claim.id: claim for claim in contract.claims}
    has_intent = any(claim.id != C0.id for claim in contract.claims)
    has_change = any(claim.kind == "CHANGE" for claim in contract.claims)
    reasons: list[str] = []
    unresolved: list[str] = []
    block = False

    for finding in findings:
        if finding.claim_id not in claims:
            raise ValueError(f"unknown claim id: {finding.claim_id!r}")
        claim = claims[finding.claim_id]
        if finding.classification == "REGRESSION" and finding.verified:
            qualifications = []
            if claim.kind == "CHANGE":
                qualifications.append(f"{claim.id} is a CHANGE claim")
            if claim.id in contested_claim_ids:
                qualifications.append(f"{claim.id} is contested")
            if not has_intent:
                qualifications.append("intent is missing")
            if qualifications:
                reasons.append(
                    f"{finding.hypothesis_id} needs review because "
                    + ", ".join(qualifications)
                    + "."
                )
            else:
                block = True
                reasons.append(
                    f"{finding.hypothesis_id} is a verified regression against "
                    f"uncontested PRESERVE claim {claim.id} with intent present."
                )
        elif finding.classification in {"INCONCLUSIVE", "EXISTING_FAILURE", "POSSIBLE_FIX"}:
            reasons.append(
                f"{finding.hypothesis_id} needs review because its classification "
                f"is {finding.classification}."
            )
        elif finding.classification == "REGRESSION":
            reasons.append(f"{finding.hypothesis_id} needs review because it is unverified.")

    for item in tamper:
        test = item.test_path + (f"::{item.test_name}" if item.test_name else "")
        if item.base_test_on_head_outcome == "FAIL":
            if has_change:
                reasons.append(
                    f"Changed test {test} fails on head but needs review "
                    "because the contract has a CHANGE claim."
                )
            else:
                block = True
                reasons.append(
                    f"Changed test {test} fails on head and the contract has no CHANGE claim."
                )
        else:
            outcome = item.base_test_on_head_outcome or "not run"
            reasons.append(f"Changed test {test} needs review because its outcome is {outcome}.")

    tested = {finding.hypothesis_id for finding in findings}
    for hypothesis in hypotheses:
        if hypothesis.id not in tested:
            risk = f"{hypothesis.id} was not tested: {hypothesis.title}"
            unresolved.append(risk)
            reasons.append(risk + ".")
    reasons.extend(errors or ())

    decision = "BLOCK" if block else "REVIEW" if reasons else "PASS"
    if decision == "PASS":
        reasons.append("No regression was found within the tested scope.")
    limitations = [
        (
            "PASS means no regression was found within the tested scope; "
            "it does not prove the change correct."
        ),
        "Only the hypotheses listed in this proof were tested.",
    ]
    if not has_intent:
        limitations.append(
            "No PR description was given, so behavior differences cannot be judged against intent."
        )
    return Verdict(
        decision=decision,
        reasons=reasons,
        unresolved_risks=unresolved,
        scope_limitations=limitations,
    )
