import html
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proofmerge.cli import app
from proofmerge.evidence import Proof
from proofmerge.report import render_html

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = ["sample_proof_block.json", "sample_proof_review.json"]


def load(name: str) -> Proof:
    return Proof.model_validate_json((FIXTURES / name).read_text())


@pytest.mark.parametrize("name", SAMPLES)
def test_samples_render_decision_claims_and_hypotheses(name: str) -> None:
    proof = load(name)
    page = render_html(proof, FIXTURES)

    assert proof.verdict.decision in page
    for reason in proof.verdict.reasons:
        assert html.escape(reason) in page
    for claim in proof.intent.claims:
        assert claim.id in page
        if claim.source_quote is not None:
            assert html.escape(claim.source_quote) in page
    for hypothesis in proof.hypotheses.hypotheses:
        assert hypothesis.id in page
        assert html.escape(hypothesis.title) in page
    for finding in proof.tamper_findings:
        assert html.escape(finding.test_path) in page


@pytest.mark.parametrize("name", SAMPLES)
def test_probe_source_missing_is_said_in_words(name: str, tmp_path: Path) -> None:
    assert "probe source not captured" in render_html(load(name), tmp_path)


def test_probe_source_is_embedded_when_captured() -> None:
    page = render_html(load("sample_proof_block.json"), FIXTURES)

    assert "sample-probe-source-marker" in page


def test_probe_source_outside_evidence_dir_is_not_read(tmp_path: Path) -> None:
    proof = load("sample_proof_block.json").model_copy(deep=True)
    proof.experiments[0].test_source_path = "../secret.txt"
    (tmp_path / "secret.txt").write_text("SECRET-OUTSIDE")

    page = render_html(proof, tmp_path)

    assert "probe source not captured" in page
    assert "SECRET-OUTSIDE" not in page


def test_claim_with_markup_is_escaped() -> None:
    proof = load("sample_proof_block.json").model_copy(deep=True)
    proof.intent.claims[1].statement = "<script>alert('xss')</script>"

    page = render_html(proof, FIXTURES)

    assert "<script>" not in page
    assert html.escape(proof.intent.claims[1].statement) in page


def test_pass_uses_scoped_wording() -> None:
    proof = load("sample_proof_review.json").model_copy(deep=True)
    proof.verdict.decision = "PASS"

    page = render_html(proof, FIXTURES)

    assert "No regression found within the tested scope" in page


def test_cli_writes_report(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    result = CliRunner().invoke(
        app, ["report", str(FIXTURES / "sample_proof_block.json"), "--out", str(out)]
    )

    assert result.exit_code == 0
    assert "BLOCK" in out.read_text()


def test_cli_rejects_missing_proof_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["report", str(tmp_path / "nope.json"), "--out", str(tmp_path / "report.html")]
    )

    assert result.exit_code != 0
