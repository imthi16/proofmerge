"""Posting a run to its pull request: inline suggestions only on lines GitHub can anchor."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from proofmerge.evidence import Proof
from proofmerge.fixes import FileFix, FixReport, FixResult
from proofmerge.pr_review import (
    ReviewError,
    build_approval,
    build_review,
    changes,
    commentable_lines,
    post_review,
)

FIXTURES = Path(__file__).parent / "fixtures"
HEAD = "CACHE = {}\n\ndef price(sku):\n    if sku in CACHE:\n        return CACHE[sku]\n    return PRICES[sku]\n"
FIXED = "CACHE = {}\n\ndef price(sku):\n    return PRICES[sku]\n"
DIFF = """diff --git a/shop.py b/shop.py
--- a/shop.py
+++ b/shop.py
@@ -1,2 +1,6 @@
+CACHE = {}
+
 def price(sku):
+    if sku in CACHE:
+        return CACHE[sku]
     return PRICES[sku]
"""


def proof() -> Proof:
    return Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())


def report(content: str = FIXED, verified: bool = True, path: str = "shop.py") -> FixReport:
    p = proof()
    fix = FixResult(hypothesis_id="H1", explanation="The cache skips the check; ask @owner.", verified=verified,
                    files=[FileFix(path=path, content=content)], patch="diff --git a/shop.py b/shop.py\n-x\n+y\n",
                    base_outcomes="PPP", fixed_outcomes="PPP" if verified else "FFF",
                    problem=None if verified else "The probe still does not pass.")
    return FixReport(run_id=p.run_id, base_sha=p.base_sha, head_sha=p.head_sha, fixes=[fix])


def test_commentable_lines_are_the_head_side_of_the_diff() -> None:
    assert commentable_lines(DIFF) == {"shop.py": {1, 2, 3, 4, 5, 6}}
    assert commentable_lines("--- a/x\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n") == {}


def test_changes_give_head_line_ranges_and_their_replacement() -> None:
    assert changes(HEAD, FIXED) == [(4, 5, [])]
    assert changes("a\nb\n", "a\nx\nb\n") == [(1, 1, ["a", "x"])]  # insertion repeats the line before
    assert changes("a\n", "z\na\n") == [(1, 1, ["z", "a"])]


def test_inline_review_puts_a_committable_suggestion_on_the_changed_lines() -> None:
    review = build_review(proof(), report(), DIFF, {"shop.py": HEAD}, "inline")
    (comment,) = review["comments"]
    assert comment | {"body": ""} == {"path": "shop.py", "start_line": 4, "line": 5, "side": "RIGHT",
                                      "start_side": "RIGHT", "body": ""}
    assert "```suggestion\n\n```" in comment["body"] and "Fix verified" in comment["body"]
    assert "@\u200bowner" in comment["body"] and "@owner" not in review["body"]  # nobody gets pinged
    assert review["event"] == "COMMENT" and review["commit_id"] == proof().head_sha
    assert "### ProofMerge: BLOCK" in review["body"] and "```diff" not in review["body"]


def test_a_change_outside_the_diff_goes_in_the_body_and_overall_mode_has_no_inline() -> None:
    top_only = "diff --git a/shop.py b/shop.py\n--- a/shop.py\n+++ b/shop.py\n@@ -0,0 +1,2 @@\n+CACHE = {}\n+\n"
    outside = build_review(proof(), report(verified=False), top_only, {"shop.py": HEAD}, "inline")
    assert "comments" not in outside and "Outside the pull request's diff" in outside["body"]
    assert "not verified" in outside["body"]
    overall = build_review(proof(), report(), DIFF, {"shop.py": HEAD}, "overall")
    assert "comments" not in overall and "```diff" in overall["body"]
    assert "No fix was suggested" in build_review(proof(), None, DIFF, {}, "overall")["body"]
    with pytest.raises(ReviewError):
        build_review(proof(), None, DIFF, {}, "approve")


def test_post_review_sends_the_json_with_gh_and_names_refusals() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout='{"html_url": "https://github.com/o/r/pull/1#r1"}', stderr="")

    url = post_review("o/r", 1, {"body": "x"}, runner)
    assert url.endswith("#r1") and calls[0][0][:5] == ["gh", "api", "--method", "POST", "repos/o/r/pulls/1/reviews"]
    assert json.loads(calls[0][1]) == {"body": "x"}
    with pytest.raises(ReviewError, match="line must be part of the diff"):
        post_review("o/r", 1, {}, lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="line must be part of the diff"))
    with pytest.raises(ReviewError):
        post_review("o/r; rm", 1, {}, runner)


def test_one_fix_shared_by_two_regressions_is_posted_once() -> None:
    shared = report()
    shared.fixes.append(shared.fixes[0].model_copy(update={"hypothesis_id": "H2"}))
    review = build_review(proof(), shared, DIFF, {"shop.py": HEAD}, "inline")
    (comment,) = review["comments"]
    assert comment["body"].startswith("**H1, H2**") and review["body"].count("#### **H1, H2**") == 1


def test_only_a_pass_run_can_approve() -> None:
    block = proof()
    with pytest.raises(ReviewError, match="only a PASS run"):
        build_approval(block)
    passed = block.model_copy(update={"verdict": block.verdict.model_copy(update={"decision": "PASS"})})
    review = build_approval(passed)
    assert review["event"] == "APPROVE" and review["commit_id"] == passed.head_sha
    assert "ProofMerge: PASS" in review["body"] and "comments" not in review


def approve_setup(monkeypatch, tmp_path: Path):
    from proofmerge import cli

    block = proof()
    passed = block.model_copy(update={"verdict": block.verdict.model_copy(update={"decision": "PASS"})})
    (tmp_path / "proof.json").write_text(passed.model_dump_json())
    monkeypatch.setattr(cli, "verify_manifest", lambda _dir: [])
    monkeypatch.setattr(cli, "_checked_commits", lambda *_: None)
    monkeypatch.setattr(cli, "rederive", lambda _proof: [])
    state = {"state": "OPEN", "headRefOid": passed.head_sha, "baseRefName": "main", "compare": "ahead"}

    def gh(argv, **_):
        if argv[1] == "api":
            return SimpleNamespace(returncode=0, stdout=state["compare"], stderr="")
        out = "o/r" if argv[1] == "repo" else json.dumps(state)
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", gh)
    args = ["approve", str(tmp_path / "proof.json"), "--pr", "7", "--repo", str(tmp_path), "--dry-run"]
    return cli, state, args


def test_approve_command_refuses_a_stale_closed_or_retargeted_pr(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    cli, state, args = approve_setup(monkeypatch, tmp_path)
    ok = CliRunner().invoke(cli.app, args)
    assert ok.exit_code == 0 and '"event": "APPROVE"' in ok.output
    healthy = dict(state)
    for change, text in (
        ({"headRefOid": "f" * 40}, "changed since this run"),
        ({"state": "MERGED"}, "merged"),
        ({"compare": "diverged"}, "does not contain the base"),
        ({"baseRefName": "x?y"}, "does not contain the base"),
    ):
        state.clear()
        state.update(healthy | change)
        bad = CliRunner().invoke(cli.app, args)
        assert bad.exit_code == 1 and text in bad.output, (change, bad.output)


def test_a_shared_fix_keeps_each_probes_own_status() -> None:
    shared = report()  # H1: verified, probe PPP -> PPP
    shared.fixes.append(shared.fixes[0].model_copy(update={
        "hypothesis_id": "H2", "verified": False, "fixed_outcomes": "FFF", "problem": "The probe still fails."}))
    review = build_review(proof(), shared, DIFF, {"shop.py": HEAD}, "inline")
    (comment,) = review["comments"]
    for text in (comment["body"], review["body"]):
        assert "**H1:** **Fix verified:**" in text
        assert "**H2:** **Suggested fix, not verified:** The probe still fails." in text
    overall = build_review(proof(), shared, DIFF, {"shop.py": HEAD}, "overall")["body"]
    assert "**H2:** **Suggested fix, not verified:**" in overall


def pony_report(*findings, **extra):
    from proofmerge.ponytail import PonytailFinding, PonytailReport

    rows = [PonytailFinding(location=loc, tag=tag, what=what, replacement=rep) for loc, tag, what, rep in findings]
    return PonytailReport(findings=rows, net_lines=12, run_id="pr-7", base_sha="a" * 40, head_sha="b" * 40, **extra)


def test_ponytail_findings_become_comments_on_their_lines_and_the_rest_go_in_the_summary() -> None:
    from proofmerge.pr_review import build_ponytail_review

    report = pony_report(
        ("shop.py:L2-4", "yagni", "One-implementation base class. Mention @team.", "Inline it."),   # inside the diff, a range
        ("shop.py:L5", "delete", "Dead helper.", "nothing"),                                        # one line, nothing replaces it
        ("other.py:L9-20", "shrink", "Loop that dict() does.", "dict(zip(a, b))"),                  # a file the diff does not touch
    )
    review = build_ponytail_review(report, DIFF)
    assert review["event"] == "COMMENT" and review["commit_id"] == "b" * 40
    first, second = review["comments"]
    assert (first["path"], first["start_line"], first["line"], first["side"]) == ("shop.py", 2, 4, "RIGHT")
    assert "@\u200bteam" in first["body"] and "Instead: Inline it." in first["body"]  # nobody gets pinged
    assert second["line"] == 5 and "start_line" not in second and "Instead" not in second["body"]
    assert "`other.py:L9-20`" in review["body"] and "dict(zip(a, b))" in review["body"]  # not on a line: in the summary
    assert review["body"].startswith("<!-- proofmerge-ponytail -->") and "3 findings" in review["body"]


def test_a_review_with_no_findings_is_not_posted() -> None:
    from proofmerge.pr_review import build_ponytail_review

    with pytest.raises(ReviewError, match="no findings"):
        build_ponytail_review(pony_report(), DIFF)


def test_ponytail_post_refuses_a_moved_pr_and_remembers_the_review_it_posted(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from proofmerge import cli
    from proofmerge.ponytail import load_report_file

    report = pony_report(("shop.py:L2-4", "yagni", "Base class.", "Inline it."))
    file = tmp_path / "pr-7.json"
    file.write_text(report.model_dump_json())
    state = {"state": "OPEN", "headRefOid": "b" * 40}
    posted = []

    def gh(argv, **kwargs):
        if argv[1] == "repo":
            return SimpleNamespace(returncode=0, stdout="o/r", stderr="")
        if argv[1:3] == ["pr", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(state), stderr="")
        return SimpleNamespace(returncode=0, stdout=DIFF, stderr="")  # gh pr diff

    monkeypatch.setattr(cli.subprocess, "run", gh)
    from proofmerge import pr_review

    monkeypatch.setattr(pr_review, "post_review",
                        lambda repo, number, review, runner=None: posted.append((repo, number, review)) or "https://github.com/o/r/pull/7#review")
    args = ["ponytail-post", str(file), "--pr", "7", "--repo", str(tmp_path)]
    state["headRefOid"] = "c" * 40
    moved = CliRunner().invoke(cli.app, args)
    assert moved.exit_code == 1 and "changed since this review" in moved.output and not posted
    state["headRefOid"] = "b" * 40
    ok = CliRunner().invoke(cli.app, args)
    assert ok.exit_code == 0 and len(posted) == 1
    assert load_report_file(file).posted_url == "https://github.com/o/r/pull/7#review"


def test_ponytail_post_rejects_an_already_posted_report(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from proofmerge import cli

    report = pony_report(("shop.py:L2-4", "yagni", "Base class.", "Inline it."),
                         posted_url="https://github.com/o/r/pull/7#review")
    file = tmp_path / "pr-7.json"
    file.write_text(report.model_dump_json())

    def gh(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", gh)
    args = ["ponytail-post", str(file), "--pr", "7", "--repo", str(tmp_path)]
    ok = CliRunner().invoke(cli.app, args)
    assert ok.exit_code == 1 and "already posted" in ok.output


def test_possible_bugs_are_posted_as_their_own_comments_and_say_no_test_was_run() -> None:
    from proofmerge.ponytail import PonytailBug
    from proofmerge.pr_review import build_ponytail_review

    bug = PonytailBug(location="shop.py:L3", tag="edge-case", what="An empty cart divides by zero. @owner", fix="Return early.")
    review = build_ponytail_review(pony_report(bugs=[bug], level="medium"), DIFF)  # a review with only a bug is allowed
    (comment,) = review["comments"]
    assert comment["line"] == 3 and "possible bug (edge-case)" in comment["body"] and "No test was run" in comment["body"]
    assert "@\u200bowner" in comment["body"] and "Try: Return early." in comment["body"]
    assert "1 possible bug" in review["body"] and "were not run" in review["body"] and "finding" not in review["body"].split("\n")[2]
