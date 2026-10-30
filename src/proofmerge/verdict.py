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
    *,
    intent_established: bool = True,
    intent_given: bool = True,
    explained_probe_ids: frozenset[str] = frozenset(),
    explained_probe_claim_ids: dict[str, str] | None = None,
    mismatched_probe_ids: frozenset[str] = frozenset(),
    wonder_checked: bool = False,
) -> Verdict:
    """Apply rules 1–9, preserving all reasons when BLOCK takes precedence."""
    claims = {claim.id: claim for claim in contract.claims}
    explicit = any(claim.id != C0.id for claim in contract.claims)
    has_intent = intent_given and not (not explicit and contract.rejected)
    has_change = any(claim.kind == "CHANGE" for claim in contract.claims)
    reasons: list[str] = []
    unresolved: list[str] = []
    block = False

    for hypothesis_id in sorted(mismatched_probe_ids):
        reasons.append(
            f"{hypothesis_id} needs review because its probe may not test its hypothesis."
        )

    for finding in findings:
        if finding.claim_id not in claims:
            raise ValueError(f"unknown claim id: {finding.claim_id!r}")
        claim = claims[finding.claim_id]
        if finding.classification == "REGRESSION" and finding.verified:
            change_id = (explained_probe_claim_ids or {}).get(finding.hypothesis_id)
            explained = finding.hypothesis_id in explained_probe_ids
            if claim.kind == "CHANGE" and wonder_checked:
                explained = change_id == claim.id
            if explained:
                change = claims.get(change_id)
                if change is not None and change.kind == "CHANGE":
                    reasons.append(f"{finding.hypothesis_id} may be the intended change {change.id}.")
                else:
                    reasons.append(f"{finding.hypothesis_id} may be the intended change.")
                continue
            # Keep the cited claim for contested checks; only its CHANGE exemption expires.
            effective_claim = C0 if claim.kind == "CHANGE" and wonder_checked else claim
            qualifications = []
            if effective_claim.kind == "CHANGE":
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
            elif finding.hypothesis_id not in mismatched_probe_ids:
                block = True
                reasons.append(
                    f"{finding.hypothesis_id} is a verified regression against "
                    f"uncontested PRESERVE claim {effective_claim.id} with intent present."
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
            if not intent_established:
                reasons.append(
                    f"Changed test {test} fails on head but needs review "
                    "because intent extraction failed."
                )
            elif has_change:
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
