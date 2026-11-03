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
