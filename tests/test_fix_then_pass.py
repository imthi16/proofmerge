"""Reproduce: verify -> BLOCK, fix -> verified, apply fix, verify again -> PASS.

The bug being fixed is that the second verify does not return PASS after a verified
fix is applied to the PR head, even though the regression is gone.
"""

import json
import subprocess
from pathlib import Path

from test_meta import FakeCompletions
from test_pipeline import Replies, assert_bundle
from test_probes import git

from proofmerge.context import get_context
from proofmerge.fixes import suggest_fixes
from proofmerge.meta import MetaClient
from proofmerge.pipeline import run_verify

INTENT = "Returned prices must not change. Add an offset constant."
PROBE = "# GOOD\nfrom calc import double\ndef test_double():\n    assert double(3) == 6\n"


def make_repo(tmp_path: Path):
    """Base: double is correct. Head: double is buggy AND an unrelated file changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def double(x):\n    return x * 2\n")
    (repo / "constants.py").write_text("OFFSET = 0\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    (repo / "calc.py").write_text("def double(x):\n    return x + 2\n")
    (repo / "constants.py").write_text("OFFSET = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    return repo, get_context(repo, "main~1", "main")


def fake_runner(calls):
    """PASS when the probe says GOOD and calc.py has the correct implementation."""

    def run(image, code_dir, probe_dir, probe_name, timeout):
        calc = (code_dir / "calc.py").read_text()
        correct = "x * 2" in calc and "x + 2" not in calc
        calls.append(calc)
        good = "GOOD" in (probe_dir / probe_name).read_text()
        if not good:
            return 1, b"E   NameError: base-says-no", b""
        if not correct:
            return 1, b"E   AssertionError: double(3) == 5", b""
        return 0, b"", b""

    return run


class ReproReplies(Replies):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make sure the hypothesis cites a changed location that survives the fix.
        self.replies[1]["hypotheses"][0]["changed_locations"] = ["calc.py::double"]


def execute(tmp_path, replies=None, runner=None):
    repo, context = make_repo(tmp_path)
    client = MetaClient(replies or ReproReplies())
    evidence = tmp_path / "evidence"
    proof = run_verify(
        repo, context.base_sha, context.head_sha, INTENT, evidence, client,
        runner=runner or fake_runner([]), digest="sha256:fake",
    )
    return repo, proof, evidence, client, context


def test_verify_fix_apply_verify_passes(tmp_path: Path):
    calls = []
    repo, proof, evidence, _client, context = execute(tmp_path, runner=fake_runner(calls))

    # Step 1: verify -> BLOCK because of a verified regression.
    assert proof.verdict.decision == "BLOCK"
    assert proof.findings[0].verified
    assert proof.findings[0].classification == "REGRESSION"
    hypothesis_id = proof.findings[0].hypothesis_id
    assert_bundle(proof, evidence)

    # Step 2: suggest a fix -> verified.
    fix_client = MetaClient(FakeCompletions(content=json.dumps({
        "explanation": "Restore multiplication.",
        "files": [{"path": "calc.py", "content": "def double(x):\n    return x * 2\n"}],
    })))

    def run_probe(sha, hid, source, dest):
        from test_fixes import experiment

        calc = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:calc.py"],
                              capture_output=True, text=True, check=True).stdout
        correct = "x * 2" in calc and "x + 2" not in calc
        return experiment(sha, "PPP", "PPP" if correct else "FFF")

    report = suggest_fixes(
        proof, evidence, repo, fix_client, context.diff_text,
        context.changed_files, run_probe, {hypothesis_id: "E   AssertionError: double(3) == 5"},
    )
    (fix_result,) = report.fixes
    assert fix_result.verified, fix_result.problem
    fix_sha = fix_result.fix_sha

    # Step 3: apply the fix commit to the head branch.
    git(repo, "checkout", "-q", "main")
    git(repo, "reset", "-q", "--hard", fix_sha)

    # Step 4: verify again -> PASS (use the branch name, as a user would).
    new_evidence = tmp_path / "evidence-fixed"
    new_proof = run_verify(
        repo, context.base_sha, "main", INTENT, new_evidence,
        MetaClient(ReproReplies()), runner=fake_runner([]), digest="sha256:fake",
    )
    assert new_proof.verdict.decision == "PASS", new_proof.verdict.reasons
