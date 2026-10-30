import html
from pathlib import Path

import pytest
from typer.testing import CliRunner

from proofmerge.cli import app
from proofmerge.evidence import Proof
from proofmerge.report import render_html, scope_receipt

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


def test_card_cites_the_verified_quote_not_the_paraphrase() -> None:
    proof = load("sample_proof_block.json")
    c2 = next(c for c in proof.intent.claims if c.id == "C2")
    card = render_html(proof, FIXTURES).split('<section class="card">')[1]

    assert html.escape(c2.source_quote) in card
    assert c2.statement not in card  # Spark's reading is not what was verified


def test_card_shows_why_head_failed() -> None:
    card = render_html(load("sample_proof_block.json"), FIXTURES).split('<section class="card">')[1]

    assert "Why head failed" in card
    assert "assert 25.0 == 35.0" in card


def test_failure_log_is_escaped_and_bounded(tmp_path: Path) -> None:
    proof = load("sample_proof_block.json")
    (tmp_path / "H1").mkdir()
    lines = ["E   <script>x</script>"] + [f"E   line {i}" for i in range(50)]
    (tmp_path / "H1" / "head-1.stdout").write_text("\n".join(lines))

    page = render_html(proof, tmp_path)

    assert "<script>x" not in page and "&lt;script&gt;x" in page
    assert "line 18" in page and "line 19" not in page  # 20 lines at most


def test_failure_log_outside_evidence_dir_is_not_read(tmp_path: Path) -> None:
    proof = load("sample_proof_block.json").model_copy(deep=True)
    for run in proof.experiments[0].executions:
        run.stdout_path = "../outside.stdout"
    (tmp_path / "outside.stdout").write_text("E   SECRET-OUTSIDE")
    (tmp_path / "ev").mkdir()

    page = render_html(proof, tmp_path / "ev")

    assert "SECRET-OUTSIDE" not in page and "Why head failed" not in page


def test_grid_rows_carry_short_shas_and_banner_the_run_id() -> None:
    proof = load("sample_proof_block.json")
    page = render_html(proof, FIXTURES)

    assert f"base <code>{proof.base_sha[:7]}</code>" in page
    assert f"head <code>{proof.head_sha[:7]}</code>" in page
    assert f"run <code>{proof.run_id}</code>" in page


def test_wonder_annotations_are_scoped_and_escape_untrusted_text():
    proof = load("sample_proof_block.json").model_copy(deep=True)
    claim = proof.intent.claims[1]
    hypothesis = proof.hypotheses.hypotheses[0]
    claim.id = "<script>claim</script>"
    claim.source_quote = "<script>quote</script>"
    hypothesis.id = "<script>hypothesis</script>"
    proof.contested_claim_ids = [claim.id]
    proof.mismatched_probe_ids = [hypothesis.id]
    page = render_html(proof, FIXTURES)
    assert page.count("contested by Muse Wonder") == 1
    assert page.count("Muse Wonder: probe may not test this hypothesis") == 1
    assert "<script>" not in page
    for value in (claim.id, claim.source_quote, hypothesis.id):
        assert html.escape(value) in page


@pytest.mark.parametrize("name", SAMPLES)
def test_old_proofs_render_without_wonder_annotations(name):
    page = render_html(load(name), FIXTURES)
    assert "contested by Muse Wonder" not in page
    assert "Muse Wonder: probe may not test this hypothesis" not in page


def test_explained_change_annotation_is_scoped_and_escaped():
    proof = load("sample_proof_block.json").model_copy(deep=True)
    hypothesis = proof.hypotheses.hypotheses[0]
    proof.explained_probe_ids = [hypothesis.id, "H99"]
    proof.explained_probe_claim_ids = {hypothesis.id: "<script>C1</script>", "H99": "C99"}
    page = render_html(proof, FIXTURES)
    assert page.count("Muse Wonder: may be the intended change") == 1
    assert "Muse Wonder: may be the intended change &lt;script&gt;C1&lt;/script&gt;" in page
    assert "<script>" not in page


def test_receipt_says_what_was_and_was_not_probed() -> None:
    proof = load("sample_proof_block.json")
    lines = scope_receipt(proof)
    assert lines[0] == "Spark proposed 3 hypotheses."
    assert "1 probe ran on base and head, 3 attempts each; 1 passed on base every time." in lines
    assert "No probe ran for H2, H3." in lines
    page = render_html(proof, FIXTURES)
    assert page.index("What this run checked") < page.index("Hypotheses and probes")


def test_receipt_flags_a_pass_with_no_probe() -> None:
    proof = load("sample_proof_block.json").model_copy(deep=True)
    proof.hypotheses.hypotheses = []
    proof.experiments, proof.findings, proof.tamper_findings = [], [], []
    proof.verdict.decision = "PASS"
    assert scope_receipt(proof) == [
        "Spark proposed 0 hypotheses.",
        "No probe ran, so nothing in this run tested the change directly.",
        "0 test files changed by the pull request were run from base on head.",
        "PASS covers only these checks.",
    ]



def test_receipt_counts_tamper_runs_per_file_not_findings() -> None:
    proof = Proof.model_validate_json(
        (Path(__file__).parent.parent / "demo/runs/2026-10-29/intended-change/proof.json").read_text()
    )
    assert len(proof.tamper_findings) == 4  # four findings, all in one test file
    lines = scope_receipt(proof)
    assert "No hypothesis probe ran; only the changed tests below were run." in lines
    assert "1 test file changed by the pull request was run from base on head." in lines
