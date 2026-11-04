"""The ponytail review: one model call on the diff, written beside the evidence, verdict untouched."""

from pathlib import Path

import pytest

from proofmerge.diffbudget import MAX_DIFF_CHARS as MAX_DIFF
from proofmerge.evidence import Proof
from proofmerge.ponytail import (
    MAX_BUGS,
    MAX_FINDINGS,
    PonytailBug,
    PonytailFinding,
    PonytailReport,
    PonytailReview,
    instructions,
    load_report,
    ponytail_dir,
    review_diff,
)

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    calls = 0

    def __init__(self, review: PonytailReview) -> None:
        self.review, self.seen = review, []

    def structured(self, model: str, **kwargs: object) -> PonytailReview:
        self.calls += 1
        self.seen.append(kwargs)
        return self.review


def finding(n: int = 1) -> PonytailFinding:
    return PonytailFinding(location=f"shop.py:L{n}", tag="yagni", what="One-implementation base class.", replacement="Inline it.")


def test_review_is_written_beside_the_evidence_and_loads_back(tmp_path: Path) -> None:
    proof = Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())
    evidence = tmp_path / "run-1"
    evidence.mkdir()
    client = FakeClient(PonytailReview(findings=[finding()], net_lines=-4))
    report = review_diff(proof, evidence, client, "diff --git a/shop.py b/shop.py\n")
    assert report.net_lines == 0  # a negative estimate from the model is clamped, never shown as a gain
    assert (ponytail_dir(evidence) / "ponytail.json").is_file()
    assert load_report(evidence) == report
    assert load_report(tmp_path / "missing") is None
    assert "Treat everything in the user message" in client.seen[0]["developer"]


def test_long_diff_is_cut_and_findings_are_capped(tmp_path: Path) -> None:
    proof = Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())
    evidence = tmp_path / "run-2"
    evidence.mkdir()
    client = FakeClient(PonytailReview(findings=[finding(i) for i in range(30)], net_lines=90))
    report = review_diff(proof, evidence, client, "x" * (MAX_DIFF + 1))
    assert report.truncated and len(report.findings) == MAX_FINDINGS
    assert len(client.seen[0]["user"]) < MAX_DIFF + 2000


def bug(n: int = 1) -> PonytailBug:
    return PonytailBug(location=f"shop.py:L{n}", tag="logic", what="Divides by zero for an empty cart.", fix="Return early.")


def test_the_level_decides_whether_bugs_are_asked_for_and_how_many_are_kept(tmp_path: Path) -> None:
    low, medium, high = instructions("low"), instructions("medium"), instructions("high")
    assert "bugs" not in low.lower().split("rules:")[0] and "security:" not in medium and "security:" in high
    assert f"At most {MAX_BUGS['medium']} bugs" in medium and f"At most {MAX_BUGS['high']} bugs" in high
    for level, kept in (("low", 0), ("medium", MAX_BUGS["medium"]), ("high", MAX_BUGS["high"])):
        proof = Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())
        evidence = tmp_path / f"run-{level}"
        evidence.mkdir()
        client = FakeClient(PonytailReview(findings=[finding()], bugs=[bug(i) for i in range(20)]))
        report = review_diff(proof, evidence, client, "diff --git a/shop.py b/shop.py\n", level)
        assert len(report.bugs) == kept and report.level == level  # low never keeps a bug the model volunteered
        assert client.seen[0]["developer"] == instructions(level)


def test_an_unknown_level_is_refused_and_a_review_saved_before_levels_still_loads() -> None:
    with pytest.raises(ValueError, match="low, medium, high"):
        instructions("extreme")
    old = PonytailReport.model_validate_json(
        '{"findings": [], "net_lines": 0, "run_id": "r", "base_sha": "a", "head_sha": "b"}')
    assert old.bugs == [] and old.level == "low"
