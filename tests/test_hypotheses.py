import json
import subprocess

import pytest
from test_meta import FakeCompletions
from typer.testing import CliRunner

from proofmerge.cli import app
from proofmerge.context import GitContext
from proofmerge.hypotheses import (
    MAX_DIFF_CHARS,
    MAX_HYPOTHESES,
    ProposedHypotheses,
    generate_hypotheses,
)
from proofmerge.intent import C0, IntentClaim, IntentContract
from proofmerge.meta import SPARK_MODEL, MetaClient

PRICING = "src/demo_shop/pricing.py"
CONTEXT = GitContext(
    base_sha="a" * 40,
    head_sha="b" * 40,
    changed_files=[PRICING],
    diff_text=f"--- a/{PRICING}\n+++ b/{PRICING}\n+_price_cache: dict[str, float] = {{}}\n",
    diff_sha256="0" * 64,
)
CONTRACT = IntentContract(
    claims=[
        C0,
        IntentClaim(
            id="C1",
            kind="PRESERVE",
            statement="Prices returned by price lookup do not change.",
            source_quote="Returned prices must not change.",
        ),
    ],
    rejected=[],
)


def hypothesis(claim_id: str = "C1", locations: list[str] | None = None) -> dict:
    return {
        "claim_id": claim_id,
        "title": "Cache key ignores tenant",
        "changed_locations": [f"{PRICING}::price_for"] if locations is None else locations,
        "suspected_behavior": "tenant-b gets tenant-a's cached price for widget",
        "expected_base": "price_for('tenant-b', 'widget') == 35.0",
        "expected_head": "returns 25.0 after tenant-a looked it up",
        "test_intent": "look up widget for tenant-a, then for tenant-b",
        "risk_category": "data-isolation",
        "falsification_criteria": "tenant-b still gets 35.0",
        "possibly_intended": False,
    }


def reply(*hypotheses: dict) -> FakeCompletions:
    return FakeCompletions(content=json.dumps({"hypotheses": list(hypotheses)}))


def test_valid_hypotheses_get_ids_and_the_request_carries_claims_and_diff():
    fake = reply(hypothesis("C1"), hypothesis("C0"))
    found = generate_hypotheses(CONTEXT, CONTRACT, MetaClient(fake))

    assert [(h.id, h.claim_id) for h in found.hypotheses] == [("H1", "C1"), ("H2", "C0")]
    assert found.rejected == []

    request = fake.requests[0]
    assert request["model"] == SPARK_MODEL
    assert request["reasoning_effort"] == "medium"
    assert request["response_format"]["json_schema"]["name"] == ProposedHypotheses.__name__
    user = request["messages"][1]["content"]
    assert "Returned prices must not change." in user
    assert CONTEXT.diff_text in user
    assert "untrusted" in request["messages"][0]["content"]


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (hypothesis(claim_id="C9"), "unknown claim 'C9'"),
        (hypothesis(locations=[]), "no changed location"),
        (hypothesis(locations=["src/demo_shop/inventory.py::reserve"]), "not in a changed file"),
        (hypothesis(locations=[f"b/{PRICING}::price_for"]), "not in a changed file"),
    ],
)
def test_hypotheses_with_unknown_claims_or_unchanged_locations_are_rejected(bad, reason):
    found = generate_hypotheses(CONTEXT, CONTRACT, MetaClient(reply(bad, hypothesis())))

    assert [h.id for h in found.hypotheses] == ["H1"]
    assert len(found.rejected) == 1
    assert reason in found.rejected[0].reason


def test_hypotheses_over_the_limit_are_rejected_not_kept():
    fake = reply(*[hypothesis() for _ in range(MAX_HYPOTHESES + 2)])
    found = generate_hypotheses(CONTEXT, CONTRACT, MetaClient(fake))

    assert len(found.hypotheses) == MAX_HYPOTHESES
    assert [r.reason for r in found.rejected] == [
        f"over the limit of {MAX_HYPOTHESES} hypotheses"
    ] * 2


def test_empty_diff_makes_no_model_call():
    fake = FakeCompletions()
    empty = CONTEXT.model_copy(update={"changed_files": [], "diff_text": ""})
    found = generate_hypotheses(empty, CONTRACT, MetaClient(fake))

    assert found.hypotheses == [] and found.rejected == []
    assert fake.requests == []


def test_oversized_diff_is_refused_before_any_model_call():
    fake = FakeCompletions()
    huge = CONTEXT.model_copy(update={"diff_text": "x" * (MAX_DIFF_CHARS + 1)})
    with pytest.raises(ValueError, match=str(MAX_DIFF_CHARS)):
        generate_hypotheses(huge, CONTRACT, MetaClient(fake))
    assert fake.requests == []


def test_hypotheses_command_fails_cleanly_on_a_bad_repo(tmp_path):
    result = CliRunner().invoke(app, ["hypotheses", str(tmp_path), "--base", "main", "--head", "x"])
    assert result.exit_code == 1
    assert "Not a Git repository" in result.output


def test_unusual_file_names_are_sent_literally_and_accepted():
    # Git C-quotes this name in diff headers, so the model must copy it from the file list.
    name = "src/café.py"
    context = CONTEXT.model_copy(
        update={"changed_files": [name], "diff_text": '+++ "b/src/caf\\303\\251.py"\n'}
    )
    fake = reply(hypothesis(locations=[f"{name}::f"]))
    found = generate_hypotheses(context, CONTRACT, MetaClient(fake))

    assert [h.id for h in found.hypotheses] == ["H1"]
    assert f'["{name}"]' in fake.requests[0]["messages"][1]["content"]


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.mark.parametrize(
    ("content", "exit_code", "message"),
    [("before\n", 0, "no changes"), ("x" * (MAX_DIFF_CHARS + 1), 1, str(MAX_DIFF_CHARS))],
)
def test_command_checks_the_diff_before_any_model_call(tmp_path, content, exit_code, message):
    git(tmp_path, "init", "-q")
    git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "base",
    )
    (tmp_path / "f.txt").write_text(content)
    git(tmp_path, "add", ".")
    git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "-m",
        "head",
    )
    base = "HEAD~1" if exit_code else "HEAD"  # HEAD..HEAD is an empty comparison
    intent = tmp_path / "intent.md"
    intent.write_text("Make it faster.")
    # An empty key proves no client is built: building one would fail with a key error.
    result = CliRunner().invoke(
        app,
        [
            "hypotheses",
            str(tmp_path),
            "--base",
            base,
            "--head",
            "HEAD",
            "--intent-file",
            str(intent),
        ],
        env={"MODEL_API_KEY": ""},
    )
    assert result.exit_code == exit_code
    assert message in result.output
    assert "MODEL_API_KEY" not in result.output
