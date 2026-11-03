"""A large diff is fitted to the model's budget, and what was left out is named and counted against PASS."""

import pytest

from proofmerge.diffbudget import MAX_DIFF_CHARS, is_generated, view_diff
from proofmerge.evidence import Finding, VerdictInputs
from proofmerge.hypotheses import Hypothesis
from proofmerge.intent import IntentClaim, IntentContract
from proofmerge.verdict import adjudicate


def section(path: str, size: int) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n" + ("+x\n" * size)


def test_a_small_diff_is_shown_whole() -> None:
    diff = section("a.py", 10)
    view = view_diff(diff)
    assert view.text == diff and view.omitted == [] and view.noted == []


def test_a_large_diff_keeps_source_first_and_names_what_it_left_out() -> None:
    diff = (
        section("package-lock.json", 30_000)  # generated: never shown
        + section("src/big.py", 30_000)  # source, but alone too large to fit with the others
        + section("src/small.py", 100)
        + section("tests/test_small.py", 100)
        + section("src/medium.py", 5_000)
    )
    view = view_diff(diff)
    assert len(view.text) < MAX_DIFF_CHARS + 1_000
    for shown in ("src/small.py", "src/medium.py", "tests/test_small.py"):
        assert f"diff --git a/{shown} " in view.text
    assert "diff --git a/src/big.py " not in view.text and "diff --git a/package-lock.json " not in view.text
    assert view.omitted == ["src/big.py"] and view.noted == ["package-lock.json"]
    assert "Source files not shown (or only partly): src/big.py." in view.text
    order = [view.text.index(f"a/{p} ") for p in ("src/small.py", "tests/test_small.py", "src/medium.py")]
    assert order == sorted(order)


def test_one_file_over_the_whole_budget_is_shown_in_part_and_counted() -> None:
    view = view_diff(section("src/huge.py", 40_000))
    assert view.omitted == ["src/huge.py"] and "diff --git a/src/huge.py " in view.text
    assert len(view.text) < MAX_DIFF_CHARS + 1_000


def test_text_that_is_not_a_git_diff_is_cut_not_dropped() -> None:
    view = view_diff("x" * (MAX_DIFF_CHARS + 5))
    assert view.text.startswith("xxx") and view.omitted


@pytest.mark.parametrize("path", ["yarn.lock", "web/dist/app.js", "a/b.min.js", "logo.svg", "src/__snapshots__/a.snap"])
def test_lock_generated_and_binary_files_are_recognised(path: str) -> None:
    assert is_generated(path)
    assert not is_generated("src/app.py")


def verdict(inputs: VerdictInputs):
    claim = IntentClaim(id="C0", kind="PRESERVE", statement="Nothing else changes.", source_quote=None)
    return adjudicate(
        IntentContract(claims=[claim], rejected=[]),
        [Hypothesis(id="H1", claim_id="C0", title="t", changed_locations=["a.py::f"], suspected_behavior="s",
                    expected_base="b", expected_head="h", test_intent="i", risk_category="r",
                    falsification_criteria="f", possibly_intended=False)],
        [Finding(hypothesis_id="H1", claim_id="C0", classification="NO_DIFFERENCE", verified=False, reproducer_command="x")],
        [],
        inputs,
    )


def base_inputs(**extra) -> VerdictInputs:
    return VerdictInputs(errors=[], intent_established=True, intent_given=True, wonder_checked=True, **extra)


def test_source_spark_never_saw_turns_a_pass_into_review_with_the_files_named() -> None:
    assert verdict(base_inputs()).decision == "PASS"
    blocked = verdict(base_inputs(unshown_files=["src/big.py"], unshown_other=["yarn.lock"]))
    assert blocked.decision == "REVIEW" and "src/big.py" in blocked.reasons[0]
    assert any("src/big.py" in note for note in blocked.scope_limitations)
    assert any("yarn.lock" in note for note in blocked.scope_limitations)


def test_generated_files_alone_do_not_block_a_pass() -> None:
    only_generated = verdict(base_inputs(unshown_other=["yarn.lock"]))
    assert only_generated.decision == "PASS"
    assert any("yarn.lock" in note for note in only_generated.scope_limitations)


def test_a_test_file_that_does_not_fit_is_noted_and_does_not_block_a_pass() -> None:
    diff = section("src/app.py", 5_000) + section("tests/test_big.py", 30_000) + section("tests/test_other.py", 30_000)
    view = view_diff(diff)
    assert view.omitted == [] and view.noted  # a test file did not fit, but no source was left out
    assert "Test, lock, generated or binary files not shown:" in view.text
    assert verdict(base_inputs(unshown_other=view.noted)).decision == "PASS"


def test_what_spark_was_not_shown_is_recorded_in_the_proof_and_replays(tmp_path, monkeypatch) -> None:
    from test_pipeline import execute

    from proofmerge import pipeline
    from proofmerge.diffbudget import DiffView
    from proofmerge.rederive import rederive

    monkeypatch.setattr(pipeline, "view_diff", lambda _text: DiffView(text="", omitted=["calc.py"], noted=["yarn.lock"]))
    proof, _evidence, _client, _context = execute(tmp_path)
    assert proof.verdict_inputs.unshown_files == ["calc.py"] and proof.verdict_inputs.unshown_other == ["yarn.lock"]
    assert any("calc.py" in note for note in proof.verdict.scope_limitations)
    assert rederive(proof) == []  # replay recomputes the same verdict from the recorded inputs
