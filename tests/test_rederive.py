"""replay recomputes findings and the verdict, so an edited and resealed proof fails."""

import json

import pytest
from test_pipeline import Replies, execute
from typer.testing import CliRunner

from proofmerge import cli
from proofmerge.dashboard import _summary
from proofmerge.evidence import Proof
from proofmerge.manifest import write_manifest
from proofmerge.rederive import rederive

pytest_plugins = ["test_sandbox"]


def reseal(evidence, change):
    """Edit proof.json the way someone covering their tracks would: then rehash."""
    path = evidence / "proof.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))
    write_manifest(evidence)
    return Proof.model_validate_json(path.read_text())


def replay(evidence):
    return CliRunner().invoke(cli.app, ["replay", str(evidence / "proof.json")])


@pytest.mark.parametrize(("replies", "decision"), [
    (Replies, "BLOCK"), (lambda: Replies(fail=2), "REVIEW"), (lambda: Replies(fail=4), "BLOCK"),
])  # fmt: skip
def test_a_fresh_proof_recomputes_exactly(tmp_path, replies, decision):
    proof, evidence, _, _ = execute(tmp_path, replies=replies())
    assert proof.verdict.decision == decision
    assert proof.verdict_inputs is not None
    assert rederive(proof) == []
    result = replay(evidence)
    assert result.exit_code == 0, result.output
    assert f"verdict recomputed from the evidence: {decision}" in result.stdout


def test_an_edited_decision_fails_even_with_a_new_manifest(tmp_path):
    _, evidence, _, _ = execute(tmp_path)

    def soften(data):
        data["verdict"]["decision"] = "PASS"

    reseal(evidence, soften)
    result = replay(evidence)
    assert result.exit_code == 1
    assert "Verdict does not follow from the evidence: recorded PASS, recomputed BLOCK." in (
        result.stdout
    )


def test_an_edited_execution_outcome_changes_the_recomputed_finding(tmp_path):
    _, evidence, _, _ = execute(tmp_path)

    def hide_failure(data):
        for execution in data["experiments"][0]["executions"]:
            if execution["revision"] == "head":
                execution["outcome"] = "PASS"

    proof = reseal(evidence, hide_failure)
    problems = rederive(proof)
    assert "H1 head attempt 1: outcome PASS does not match exit code 1." in problems
    assert any("recorded H1 REGRESSION verified, recomputed H1 NO_DIFFERENCE" in p
               for p in problems)
    assert any("recorded BLOCK, recomputed PASS" in p for p in problems)


def test_an_edited_reason_is_caught(tmp_path):
    _, evidence, _, _ = execute(tmp_path, replies=Replies(fail=2))

    def drop_reason(data):
        data["verdict"]["reasons"] = data["verdict"]["reasons"][1:]

    proof = reseal(evidence, drop_reason)
    assert rederive(proof) == [
        "Verdict REVIEW matches, but its recorded reasons or limits differ from the recomputed ones."
    ]


def test_documented_limit_a_consistent_rewrite_of_the_inputs_passes(tmp_path):
    """Recomputation proves the verdict follows from the records, not that they are true."""
    _, evidence, _, _ = execute(tmp_path, replies=Replies(fail=2))

    def hide_error(data):
        data["verdict_inputs"]["errors"] = []
        data["verdict"]["decision"] = "PASS"
        data["verdict"]["reasons"] = ["No regression was found within the tested scope."]

    proof = reseal(evidence, hide_error)
    assert rederive(proof) == []  # only re-execution (replay --rerun) or signatures stop this


def test_a_finding_citing_an_unknown_claim_cannot_be_recomputed(tmp_path):
    _, evidence, _, _ = execute(tmp_path)

    def bad_claim(data):
        data["hypotheses"]["hypotheses"][0]["claim_id"] = "C9"

    proof = reseal(evidence, bad_claim)
    assert rederive(proof) == ["Cannot recompute the verdict: unknown claim id: 'C9'."]


def test_proofs_without_inputs_are_not_recomputed(tmp_path):
    proof, _, _, _ = execute(tmp_path)
    old = proof.model_copy(update={"verdict_inputs": None})
    assert rederive(old) is None


def test_dashboard_marks_an_edited_verdict_as_changed(tmp_path):
    _, evidence, _, _ = execute(tmp_path)
    assert _summary("run", evidence)["integrity"] == {"status": "intact", "problems": []}

    def soften(data):
        data["verdict"]["decision"] = "PASS"

    reseal(evidence, soften)
    integrity = _summary("run", evidence)["integrity"]
    assert integrity["status"] == "changed"
    assert any("recorded PASS, recomputed BLOCK" in p for p in integrity["problems"])
