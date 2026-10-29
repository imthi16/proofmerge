import pytest

from proofmerge.evidence import Classification, Execution, Experiment, Outcome
from proofmerge.findings import classify, findings_for
from proofmerge.hypotheses import Hypothesis
from proofmerge.probes import Probe


def experiment(
    base: list[Outcome],
    head: list[Outcome],
    hypothesis_id: str = "H1",
    source_hash: str = "a" * 64,
) -> Experiment:
    executions = [
        Execution(
            revision=revision,
            commit_sha="b" * 40 if revision == "base" else "c" * 40,
            attempt=attempt,
            command_argv=["python", "-m", "pytest", "-q"],
            exit_code={"PASS": 0, "FAIL": 1, "ERROR": 6, "TIMEOUT": None}[outcome],
            outcome=outcome,
            duration_ms=1,
            stdout_path=f"{hypothesis_id}/{revision}-{attempt}.stdout",
            stderr_path=f"{hypothesis_id}/{revision}-{attempt}.stderr",
        )
        for revision, outcomes in (("base", base), ("head", head))
        for attempt, outcome in enumerate(outcomes, 1)
    ]
    return Experiment(
        hypothesis_id=hypothesis_id,
        test_source_path=f"probes/test_{hypothesis_id.lower()}.py",
        test_source_sha256=source_hash,
        timeout_seconds=60,
        executions=executions,
    )


def hypothesis(hypothesis_id: str = "H1", claim_id: str = "C1") -> Hypothesis:
    return Hypothesis(
        id=hypothesis_id,
        claim_id=claim_id,
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


def probe(hypothesis_id: str = "H1", passed: bool = True) -> Probe:
    return Probe(
        hypothesis_id=hypothesis_id,
        test_source="def test_price(): assert True\n",
        drafts=1,
        passed_on_base=passed,
    )


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        ("PASS", "FAIL", "REGRESSION"),
        ("FAIL", "PASS", "POSSIBLE_FIX"),
        ("FAIL", "FAIL", "EXISTING_FAILURE"),
        ("PASS", "PASS", "NO_DIFFERENCE"),
    ],
)
def test_classification_table(base: Outcome, head: Outcome, expected: Classification):
    recorded = experiment([base] * 3, [head] * 3)
    assert classify(recorded) == expected
    finding = findings_for([recorded], [hypothesis()], [probe()], "proof.json")[0]
    assert finding.classification == expected
    assert finding.verified == (expected == "REGRESSION")


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("outcome", ["ERROR", "TIMEOUT"])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_any_error_or_timeout_is_inconclusive(side: str, outcome: Outcome, position: int):
    base: list[Outcome] = ["PASS"] * 3
    head: list[Outcome] = ["FAIL"] * 3
    (base if side == "base" else head)[position] = outcome
    recorded = experiment(base, head)
    assert classify(recorded) == "INCONCLUSIVE"
    assert not findings_for([recorded], [hypothesis()], [probe()], "proof.json")[0].verified


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_disagreeing_runs_are_inconclusive(side: str, position: int):
    base: list[Outcome] = ["PASS"] * 3
    head: list[Outcome] = ["FAIL"] * 3
    (base if side == "base" else head)[position] = "FAIL" if side == "base" else "PASS"
    assert classify(experiment(base, head)) == "INCONCLUSIVE"


@pytest.mark.parametrize("base_count", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("head_count", [0, 1, 2, 3, 4])
def test_exactly_three_runs_per_side_are_required(base_count: int, head_count: int):
    expected = "REGRESSION" if base_count == head_count == 3 else "INCONCLUSIVE"
    assert classify(experiment(["PASS"] * base_count, ["FAIL"] * head_count)) == expected


def test_execution_order_does_not_change_classification():
    recorded = experiment(["PASS"] * 3, ["FAIL"] * 3)
    recorded.executions = recorded.executions[::2] + recorded.executions[1::2]
    assert classify(recorded) == "REGRESSION"


@pytest.mark.parametrize("probes", [[], [probe(passed=False)], [probe("H2")]])
def test_regression_needs_a_matching_base_passing_probe(probes: list[Probe]):
    findings = findings_for(
        [experiment(["PASS"] * 3, ["FAIL"] * 3)], [hypothesis()], probes, "proof.json"
    )
    assert findings[0].classification == "REGRESSION"
    assert not findings[0].verified


@pytest.mark.parametrize(
    "source_hash",
    ["", "a" * 63, "a" * 65, "g" * 64, "a" * 63 + "\n", " " + "a" * 64, "０" * 64],
)
def test_regression_needs_a_64_character_hex_hash(source_hash: str):
    recorded = experiment(["PASS"] * 3, ["FAIL"] * 3, source_hash=source_hash)
    finding = findings_for([recorded], [hypothesis()], [probe()], "proof.json")[0]
    assert finding.classification == "REGRESSION"
    assert not finding.verified


@pytest.mark.parametrize("source_hash", ["a" * 64, "ABCDEF0123456789" * 4])
def test_valid_regression_is_verified_and_tied_to_its_claim(source_hash: str):
    recorded = experiment(["PASS"] * 3, ["FAIL"] * 3, source_hash=source_hash)
    finding = findings_for([recorded], [hypothesis()], [probe()], "evidence/proof.json")[0]
    assert finding.model_dump() == {
        "hypothesis_id": "H1",
        "claim_id": "C1",
        "classification": "REGRESSION",
        "verified": True,
        "reproducer_command": "proofmerge replay evidence/proof.json",
    }


def test_one_finding_per_h_experiment_in_input_order():
    experiments = [
        experiment(["PASS"] * 3, ["PASS"] * 3, "H2"),
        experiment(["PASS"] * 3, ["FAIL"] * 3),
        experiment(["FAIL"] * 3, ["FAIL"] * 3, "H2"),
    ]
    findings = findings_for(
        experiments, [hypothesis(), hypothesis("H2", "C2")], [probe()], "proof.json"
    )
    assert [(f.hypothesis_id, f.claim_id) for f in findings] == [
        ("H2", "C2"),
        ("H1", "C1"),
        ("H2", "C2"),
    ]
    assert [f.classification for f in findings] == [
        "NO_DIFFERENCE",
        "REGRESSION",
        "EXISTING_FAILURE",
    ]


@pytest.mark.parametrize("hypothesis_id", ["T1", "other", ""])
def test_non_h_experiments_are_skipped_before_hypothesis_lookup(hypothesis_id: str):
    recorded = experiment(["PASS"] * 3, ["FAIL"] * 3, hypothesis_id)
    assert findings_for([recorded], [], [], "proof.json") == []


def test_unknown_hypothesis_raises_value_error():
    recorded = experiment(["PASS"] * 3, ["FAIL"] * 3, "H99")
    with pytest.raises(ValueError, match="unknown hypothesis id: 'H99'"):
        findings_for([recorded], [hypothesis()], [probe()], "proof.json")


def test_empty_experiments_produce_no_findings():
    assert findings_for([], [], [], "proof.json") == []
