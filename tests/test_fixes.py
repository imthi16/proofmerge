"""Suggested fixes: proposals are checked, built as a commit without a checkout, and verified
only when the failing probe passes every attempt. Real git, fake model, fake sandbox."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from proofmerge.evidence import Execution, Experiment, Proof
from proofmerge.fixes import (
    FileFix,
    FixError,
    ProposedFix,
    check_proposal,
    commit_fix,
    fix_dir,
    is_test_path,
    load_report,
    suggest_fixes,
)
from proofmerge.manifest import verify_manifest, write_manifest

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "def price(sku):\n    return PRICES[sku]\n"
HEAD = "CACHE = {}\n\ndef price(sku):\n    if sku in CACHE:\n        return CACHE[sku]\n    return PRICES[sku]\n"
FIXED = "CACHE = {}\n\ndef price(sku):\n    return PRICES[sku]\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "T")
    (path / "shop.py").write_text(BASE)
    (path / "tests").mkdir()
    (path / "tests" / "test_shop.py").write_text("def test_ok():\n    assert True\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    base = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-qb", "change")
    (path / "shop.py").write_text(HEAD)
    (path / "tests" / "test_shop.py").write_text("def test_ok():\n    assert 1\n")
    git(path, "commit", "-qam", "head")
    return path, base, git(path, "rev-parse", "HEAD")


class FakeClient:
    endpoint = "injected"

    def __init__(self, *replies: ProposedFix) -> None:
        self.replies, self.calls, self.histories = list(replies), 0, []

    def structured(self, model: str, **kwargs: object) -> ProposedFix:
        self.calls += 1
        self.histories.append(list(kwargs["history"]))
        return self.replies.pop(0)


def experiment(sha: str, base: str, fixed: str) -> Experiment:
    runs = [Execution(revision=rev, commit_sha=sha, attempt=i + 1, command_argv=["pytest"], exit_code=0,
                      outcome="PASS" if o == "P" else "FAIL", duration_ms=1, stdout_path="x", stderr_path="x")
            for rev, outcomes in (("base", base), ("head", fixed)) for i, o in enumerate(outcomes)]
    return Experiment(hypothesis_id="H1", test_source_path="probes/test_h1.py", test_source_sha256="0" * 64,
                      timeout_seconds=60, executions=runs)


def proof_for(base: str, head: str) -> Proof:
    proof = Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())
    return proof.model_copy(update={"base_sha": base, "head_sha": head})


def evidence(tmp_path: Path, proof: Proof) -> Path:
    run = tmp_path / "runs" / "pr-1"
    shutil.copytree(FIXTURES / "probes", run / "probes")
    (run / "proof.json").write_text(proof.model_dump_json())
    write_manifest(run)
    return run


def test_test_files_are_recognised() -> None:
    for path in ("tests/test_shop.py", "test_x.py", "src/a_test.go", "web/x.test.ts", "x.spec.js", "conftest.py"):
        assert is_test_path(path), path
    for path in ("shop.py", "src/testing_utils.py", "contest.py"):
        assert not is_test_path(path), path


def test_proposals_may_only_change_the_prs_non_test_files() -> None:
    allowed = {"shop.py": HEAD}
    ok = check_proposal(ProposedFix(explanation="x", files=[FileFix(path="shop.py", content=FIXED)]), allowed)
    assert [f.path for f in ok] == ["shop.py"]
    for files, problem in (
        ([], "no safe fix"),
        ([FileFix(path="other.py", content="x")], "did not change"),
        ([FileFix(path="shop.py", content=HEAD)], "does not change anything"),
        ([FileFix(path="shop.py", content=FIXED), FileFix(path="shop.py", content=FIXED)], "twice"),
    ):
        with pytest.raises(FixError, match=problem):
            check_proposal(ProposedFix(explanation="x", files=files), allowed)
    with pytest.raises(FixError, match="test file"):
        check_proposal(ProposedFix(explanation="x", files=[FileFix(path="tests/test_shop.py", content="")]),
                       {"tests/test_shop.py": "a"})


def test_the_fix_is_a_commit_under_refs_proofmerge_and_nothing_else_moves(repo) -> None:
    path, _base, head = repo
    before = git(path, "status", "--porcelain"), git(path, "rev-parse", "change")
    sha = commit_fix(path, head, [FileFix(path="shop.py", content=FIXED)], "refs/proofmerge/fix/x/H1")
    assert git(path, "rev-parse", "refs/proofmerge/fix/x/H1") == sha
    assert git(path, "rev-parse", f"{sha}^") == head
    assert git(path, "show", f"{sha}:shop.py") + "\n" == FIXED
    assert git(path, "show", f"{sha}:tests/test_shop.py") == git(path, "show", f"{head}:tests/test_shop.py")
    assert (git(path, "status", "--porcelain"), git(path, "rev-parse", "change")) == before
    assert (path / "shop.py").read_text() == HEAD  # the working tree is untouched
    with pytest.raises(FixError):
        commit_fix(path, head, [], "refs/heads/main")


def test_a_fix_is_verified_only_when_the_probe_passes_everywhere(repo, tmp_path: Path) -> None:
    path, base, head = repo
    proof = proof_for(base, head)
    run = evidence(tmp_path, proof)
    client = FakeClient(ProposedFix(explanation="Still caches.", files=[FileFix(path="shop.py", content=HEAD + "#\n")]),
                        ProposedFix(explanation="Drops the cache @team.", files=[FileFix(path="shop.py", content=FIXED)]))
    outcomes = iter([("PPP", "FFF"), ("PPP", "PPP")])
    seen = []

    def run_probe(sha: str, hid: str, source: str, dest: Path) -> Experiment:
        seen.append((sha, hid, source))
        return experiment(sha, *next(outcomes))

    report = suggest_fixes(proof, run, path, client, "diff", ["shop.py", "tests/test_shop.py"], run_probe, {"H1": "E fail"})
    (fix,) = report.fixes
    assert fix.verified and fix.drafts == 2 and fix.fixed_outcomes == "PPP" and fix.problem is None
    assert "FFF" in client.histories[1][-1]["content"]  # the second draft hears why the first failed
    assert seen[0][2] == (run / "probes" / "test_h1.py").read_text()
    assert fix.patch.startswith("diff --git a/shop.py b/shop.py")
    assert load_report(run) == report and json.loads((fix_dir(run) / "fix.json").read_text())["calls"] == 2
    assert verify_manifest(run) == []  # the sealed evidence is untouched


def test_a_rejected_or_failing_fix_says_why(repo, tmp_path: Path) -> None:
    path, base, head = repo
    proof = proof_for(base, head)
    run = evidence(tmp_path, proof)
    client = FakeClient(ProposedFix(explanation="x", files=[FileFix(path="tests/test_shop.py", content="")]),
                        ProposedFix(explanation="y", files=[FileFix(path="shop.py", content=FIXED)]))
    report = suggest_fixes(proof, run, path, client, "diff", ["shop.py", "tests/test_shop.py"],
                           lambda sha, hid, source, dest: experiment(sha, "PPP", "PFP"), {})
    (fix,) = report.fixes
    assert not fix.verified and "base PPP, fix PFP" in fix.problem
    assert "did not change" in client.histories[1][-1]["content"]  # tests are not offered at all


def test_doubted_or_possibly_intended_probes_get_no_fix() -> None:
    from proofmerge.fixes import regressions

    proof = proof_for("a" * 40, "b" * 40)
    assert regressions(proof) == ["H1"]
    assert regressions(proof.model_copy(update={"mismatched_probe_ids": ["H1"]})) == []
    assert regressions(proof.model_copy(update={"explained_probe_ids": ["H1"]})) == []
