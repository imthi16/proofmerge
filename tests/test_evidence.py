import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from proofmerge.evidence import Proof

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["sample_proof_block.json", "sample_proof_review.json"])
def test_sample_proofs_match_the_contract(name):
    proof = Proof.model_validate_json((FIXTURES / name).read_text())
    assert proof.intent.claims[0].id == "C0"
    assert {e.revision for e in proof.experiments[0].executions} == {"base", "head"}


def test_unknown_decision_is_rejected():
    data = json.loads((FIXTURES / "sample_proof_block.json").read_text())
    data["verdict"]["decision"] = "MERGE"
    with pytest.raises(ValidationError):
        Proof.model_validate(data)
