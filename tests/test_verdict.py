import pytest

from proofmerge.evidence import Classification, Finding, Outcome, TamperFinding
from proofmerge.hypotheses import Hypothesis
from proofmerge.intent import C0, ClaimKind, IntentClaim, IntentContract
from proofmerge.verdict import decide

SCOPE = [
    (
        "PASS means no regression was found within the tested scope; "
        "it does not prove the change correct."
    ),
    "Only the hypotheses listed in this proof were tested.",
]
NO_INTENT = (
    "No PR description was given, so behavior differences cannot be judged against intent."
)


def contract(kind: ClaimKind = "PRESERVE") -> IntentContract:
    return IntentContract(
        claims=[
            C0,
            IntentClaim(
                id="C2", kind=kind, statement="Price behavior", source_quote="Price behavior stays"
            ),
        ],
        rejected=[],
    )


def finding(
    claim_id: str = "C2",
    classification: Classification = "REGRESSION",
    verified: bool = True,
    hypothesis_id: str = "H1",
) -> Finding:
    return Finding(
        hypothesis_id=hypothesis_id,
        claim_id=claim_id,
        classification=classification,
        verified=verified,
        reproducer_command="proofmerge replay evidence/proof.json",
    )


def tamper(outcome: Outcome | None = "FAIL") -> TamperFinding:
    return TamperFinding(
        test_path="tests/test_pricing.py",
        test_name="test_discount",
        kind="MODIFIED",
        base_test_on_head_outcome=outcome,
    )


def hypothesis(hypothesis_id: str = "H1") -> Hypothesis:
    return Hypothesis(
        id=hypothesis_id,
        claim_id="C2",
        title="Tenant prices stay separate",
        changed_locations=["pricing.py::price_for"],
        suspected_behavior="Cache shares prices between tenants",
        expected_base="Each tenant gets its own price",
        expected_head="A tenant gets another tenant's price",
        test_intent="Query two tenants for the same SKU",
        risk_category="correctness",
        falsification_criteria="Both tenants receive their own prices",
        possibly_intended=False,
    )


@pytest.mark.parametrize("claim_id", ["C0", "C2"])
def test_rule_1_verified_uncontested_preserve_with_intent_blocks(claim_id):
    result = decide(contract(), [], [finding(claim_id)], [])
    assert result.decision == "BLOCK"
    assert len(result.reasons) == 1
    assert claim_id in result.reasons[0]
    assert result.scope_limitations == SCOPE


@pytest.mark.parametrize("has_intent", [False, True])
def test_rule_2_tamper_fail_without_change_blocks(has_intent):
    intent = contract() if has_intent else IntentContract(claims=[C0], rejected=[])
    result = decide(intent, [], [], [tamper()])
    assert result.decision == "BLOCK"
    assert "tests/test_pricing.py::test_discount" in result.reasons[0]


@pytest.mark.parametrize("qualification", ["CHANGE", "contested", "missing intent"])
def test_rule_3_model_signals_downgrade_verified_regression_to_review(qualification):
    intent = contract("CHANGE" if qualification == "CHANGE" else "PRESERVE")
    contested = frozenset({"C2"}) if qualification == "contested" else frozenset()
    claim_id = "C2"
    if qualification == "missing intent":
        intent = IntentContract(claims=[C0], rejected=[])
        claim_id = "C0"
    result = decide(
        intent, [], [finding(claim_id)], [], contested,
        intent_given=qualification != "missing intent",
    )
    assert result.decision == "REVIEW"
    assert len(result.reasons) == 1
    if qualification == "missing intent":
        assert result.scope_limitations == SCOPE + [NO_INTENT]


def test_rule_4_any_change_claim_downgrades_tamper_fail_to_review():
    result = decide(contract("CHANGE"), [], [], [tamper()])
    assert result.decision == "REVIEW"
    assert "CHANGE" in result.reasons[0]


def test_tamper_fail_is_review_when_intent_extraction_failed():
    result = decide(
        IntentContract(claims=[C0], rejected=[]), [], [], [tamper()], intent_established=False
    )
    assert result.decision == "REVIEW"
    assert "intent extraction failed" in result.reasons[0]


@pytest.mark.parametrize("outcome", ["PASS", "ERROR", "TIMEOUT", None])
def test_rule_5_tamper_without_fail_needs_review(outcome):
    result = decide(contract(), [], [], [tamper(outcome)])
    assert result.decision == "REVIEW"
    assert len(result.reasons) == 1


@pytest.mark.parametrize(
    "classification", ["INCONCLUSIVE", "EXISTING_FAILURE", "POSSIBLE_FIX", "REGRESSION"]
)
def test_rule_6_inconclusive_or_unverified_finding_needs_review(classification):
    result = decide(contract(), [], [finding(classification=classification, verified=False)], [])
    assert result.decision == "REVIEW"
    assert len(result.reasons) == 1


def test_rule_7_untested_hypotheses_are_unresolved_risks():
    hypotheses = [hypothesis(), hypothesis("H2"), hypothesis("H3")]
    result = decide(
        contract(), hypotheses, [finding(classification="NO_DIFFERENCE", verified=False)], []
    )
    assert result.decision == "REVIEW"
    assert result.unresolved_risks == [
        "H2 was not tested: Tenant prices stay separate",
        "H3 was not tested: Tenant prices stay separate",
    ]
    assert result.reasons == [risk + "." for risk in result.unresolved_risks]


def test_rule_8_errors_are_copied_verbatim():
    errors = ["Runner unavailable", "API failure."]
    result = decide(contract(), [], [], [], errors=errors)
    assert result.decision == "REVIEW"
    assert result.reasons == errors
    assert errors == ["Runner unavailable", "API failure."]
    assert decide(contract(), [], [], []).decision == "PASS"


def test_rule_9_no_difference_with_a_finding_for_each_hypothesis_passes():
    result = decide(
        contract(), [hypothesis()], [finding(classification="NO_DIFFERENCE", verified=False)], []
    )
    assert result.decision == "PASS"
    assert result.unresolved_risks == []
    assert result.reasons == ["No regression was found within the tested scope."]


def test_block_wins_and_keeps_reasons_from_all_fired_rules():
    result = decide(
        contract(),
        [hypothesis("H3")],
        [finding(), finding(classification="INCONCLUSIVE", verified=False, hypothesis_id="H2")],
        [tamper(), tamper("ERROR")],
        errors=["Runner unavailable"],
    )
    assert result.decision == "BLOCK"
    assert len(result.reasons) == 6
    assert "H1" in result.reasons[0]
    assert "H2" in result.reasons[1]
    assert "no CHANGE" in result.reasons[2]
    assert "ERROR" in result.reasons[3]
    assert "H3 was not tested" in result.reasons[4]
    assert result.reasons[5] == "Runner unavailable"
    assert result.unresolved_risks == ["H3 was not tested: Tenant prices stay separate"]


@pytest.mark.parametrize("claims", [[], [C0]])
def test_empty_input_passes_with_scope_limitations(claims):
    result = decide(IntentContract(claims=claims, rejected=[]), [], [], [], intent_given=False)
    assert result.decision == "PASS"
    assert result.scope_limitations == SCOPE + [NO_INTENT]
    assert result.unresolved_risks == []


def test_unknown_finding_claim_raises_even_after_a_block():
    with pytest.raises(ValueError, match="unknown claim id: 'C99'"):
        decide(contract(), [], [finding(), finding("C99")], [])


def test_unrelated_change_claim_does_not_downgrade_preserve_regression():
    intent = contract()
    intent.claims.append(contract("CHANGE").claims[1].model_copy(update={"id": "C3"}))
    result = decide(intent, [], [finding()], [])
    assert result.decision == "BLOCK"


def test_preserve_block_keeps_change_tamper_review_reason():
    intent = contract()
    intent.claims.append(contract("CHANGE").claims[1].model_copy(update={"id": "C3"}))
    result = decide(intent, [], [finding()], [tamper()])
    assert result.decision == "BLOCK"
    assert len(result.reasons) == 2
    assert "uncontested PRESERVE" in result.reasons[0]
    assert "needs review" in result.reasons[1]


def test_contested_change_claim_still_downgrades_tamper_fail():
    result = decide(contract("CHANGE"), [], [], [tamper()], frozenset({"C2"}))
    assert result.decision == "REVIEW"


def test_tamper_block_wins_over_a_contested_regression():
    result = decide(contract(), [], [finding()], [tamper()], frozenset({"C2"}))
    assert result.decision == "BLOCK"
    assert len(result.reasons) == 2
    assert "contested" in result.reasons[0]


def test_demo_tenant_cache_blocks():
    result = decide(contract(), [hypothesis()], [finding("C2")], [])
    assert result.decision == "BLOCK"


def test_demo_test_tamper_blocks():
    result = decide(contract(), [], [], [tamper()])
    assert result.decision == "BLOCK"


def test_demo_intended_change_needs_review():
    result = decide(contract("CHANGE"), [], [], [tamper()])
    assert result.decision == "REVIEW"


def test_mismatched_verified_probe_is_review_with_required_reason():
    result = decide(contract(), [], [finding()], [], mismatched_probe_ids=frozenset({"H1"}))
    assert result.decision == "REVIEW"
    assert result.reasons == [
        "H1 needs review because its probe may not test its hypothesis."
    ]


@pytest.mark.parametrize("other_evidence", ["regression", "tamper"])
def test_mismatched_probe_does_not_remove_another_block(other_evidence):
    findings = [finding()]
    if other_evidence == "regression":
        findings.append(finding(hypothesis_id="H2"))
    result = decide(
        contract(), [], findings, [tamper()] if other_evidence == "tamper" else [],
        mismatched_probe_ids=frozenset({"H1"}),
    )
    assert result.decision == "BLOCK"
    assert "H1 needs review because its probe may not test its hypothesis." in result.reasons


def test_wonder_agreement_cannot_promote_change_claim_to_block():
    result = decide(contract("CHANGE"), [], [finding()], [], mismatched_probe_ids=frozenset())
    assert result.decision == "REVIEW"


@pytest.mark.parametrize("classification, verified", [
    ("NO_DIFFERENCE", False), ("REGRESSION", True), ("REGRESSION", False),
    ("INCONCLUSIVE", False), ("EXISTING_FAILURE", False), ("POSSIBLE_FIX", False),
])
def test_probe_mismatch_requires_review_for_every_classification(classification, verified):
    result = decide(
        contract(), [hypothesis()],
        [finding(classification=classification, verified=verified)], [],
        mismatched_probe_ids=frozenset({"H1"}),
    )
    assert result.decision == "REVIEW"
    assert result.reasons.count(
        "H1 needs review because its probe may not test its hypothesis."
    ) == 1


def test_passing_mismatched_probe_keeps_unrelated_regression_block():
    result = decide(
        contract(), [hypothesis(), hypothesis("H2")],
        [finding(classification="NO_DIFFERENCE", verified=False), finding(hypothesis_id="H2")],
        [], mismatched_probe_ids=frozenset({"H1"}),
    )
    assert result.decision == "BLOCK"
    assert "H1 needs review because its probe may not test its hypothesis." in result.reasons


@pytest.mark.parametrize("intent_given, rejected, expected", [
    (True, False, "BLOCK"), (False, False, "REVIEW"), (True, True, "REVIEW"),
])
def test_c0_intent_presence_and_rejected_claims(intent_given, rejected, expected):
    from proofmerge.intent import ProposedClaim

    intent = IntentContract(claims=[C0], rejected=[ProposedClaim(
        kind="PRESERVE", statement="invented", source_quote="not in the intent",
    )] if rejected else [])
    result = decide(intent, [], [finding("C0")], [], intent_given=intent_given)
    assert result.decision == expected
    assert (NO_INTENT in result.scope_limitations) == (expected == "REVIEW")


@pytest.mark.parametrize("claim_id", ["C0", "C2"])
@pytest.mark.parametrize("other_block", [False, True])
def test_explained_regression_is_review_and_preserves_other_blocks(claim_id, other_block):
    intent = contract()
    intent.claims.append(contract("CHANGE").claims[1].model_copy(update={"id": "C1"}))
    findings = [finding(claim_id)] + ([finding(hypothesis_id="H2")] if other_block else [])
    result = decide(intent, [], findings, [], explained_probe_ids=frozenset({"H1"}),
                    explained_probe_claim_ids={"H1": "C1"})
    assert result.decision == ("BLOCK" if other_block else "REVIEW")
    assert "H1 may be the intended change C1." in result.reasons


def test_explanation_cannot_turn_inconclusive_evidence_into_block_or_pass():
    result = decide(contract(), [], [finding(classification="INCONCLUSIVE", verified=False)], [],
                    explained_probe_ids=frozenset({"H1"}))
    assert result.decision == "REVIEW"
