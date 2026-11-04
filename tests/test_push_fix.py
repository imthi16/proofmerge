"""push-fix: verified fixes go onto the author's own PR branch as one commit, never forced."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from proofmerge.evidence import Proof
from proofmerge.fixes import FileFix, FixError, FixReport, FixResult, verified_files

FIXTURES = Path(__file__).parent / "fixtures"
REAL_RUN = subprocess.run


def git(repo: Path, *args: str) -> str:
    return REAL_RUN(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def fixed(hid: str, path: str, content: str, verified: bool = True) -> FixResult:
    return FixResult(hypothesis_id=hid, explanation=f"restore {path}", verified=verified,
                     files=[FileFix(path=path, content=content)])


def test_verified_fixes_combine_and_disagreeing_ones_are_refused() -> None:
    report = FixReport(run_id="r", base_sha="a" * 40, head_sha="b" * 40, fixes=[
        fixed("H1", "a.py", "x\n"), fixed("H2", "b.py", "y\n"), fixed("H3", "c.py", "z\n", verified=False)])
    ids, files = verified_files(report)
    assert ids == ["H1", "H2"] and [f.path for f in files] == ["a.py", "b.py"]  # an unverified fix is left out
    report.fixes.append(fixed("H4", "a.py", "other\n"))
    with pytest.raises(FixError, match="Two fixes change a.py differently"):
        verified_files(report)
    with pytest.raises(FixError, match="no verified fix"):
        verified_files(FixReport(run_id="r", base_sha="a" * 40, head_sha="b" * 40, fixes=[]))


@pytest.fixture
def setup(monkeypatch, tmp_path: Path):
    from proofmerge import cli

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "shop.py").write_text("def price():\n    return 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "shop.py").write_text("def price():\n    return 2\n")
    git(repo, "commit", "-qam", "head")
    head = git(repo, "rev-parse", "HEAD")

    run = tmp_path / "run"
    run.mkdir()
    proof = Proof.model_validate_json((FIXTURES / "sample_proof_block.json").read_text())
    (run / "proof.json").write_text(proof.model_copy(update={"base_sha": base, "head_sha": head}).model_dump_json())
    fix_dir = tmp_path / "run.fix"
    fix_dir.mkdir()
    (fix_dir / "fix.json").write_text(FixReport(run_id="r", base_sha=base, head_sha=head, fixes=[
        fixed("H1", "shop.py", "def price():\n    return 1\n")]).model_dump_json())
    monkeypatch.setattr(cli, "verify_manifest", lambda _dir: [])

    state = {"state": "OPEN", "headRefOid": head, "headRefName": "agent/x", "isCrossRepository": False,
             "author": {"login": "me"}, "me": "me"}
    pushes: list[list[str]] = []

    def run_cmd(argv, **kwargs):
        if argv[0] == "gh":
            out = {"repo": "o/r", "api": state["me"]}.get(argv[1]) or json.dumps(state)
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        if "push" in argv:
            pushes.append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return REAL_RUN(argv, **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", run_cmd)
    args = ["push-fix", str(run / "proof.json"), "--pr", "7", "--repo", str(repo)]
    return cli, repo, head, state, pushes, args


def test_push_fix_pushes_one_commit_on_the_pr_head_without_force(setup) -> None:
    cli, repo, head, _state, pushes, args = setup
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    (argv,) = pushes
    assert "--force" not in argv and "-f" not in argv and argv[-2] == "https://github.com/o/r.git"
    sha, ref = argv[-1].split(":")
    assert ref == "refs/heads/agent/x" and git(repo, "rev-parse", f"{sha}^") == head  # a fast-forward of the PR
    assert git(repo, "show", f"{sha}:shop.py") == "def price():\n    return 1"
    assert git(repo, "log", "-1", "--format=%s", sha) == "Fix H1 found by ProofMerge"


@pytest.mark.parametrize(("change", "text"), [
    ({"author": {"login": "someone"}}, "opened by @someone and you are signed in as @me"),
    ({"headRefOid": "f" * 40}, "changed since this run"),
    ({"state": "MERGED"}, "merged"),
    ({"isCrossRepository": True}, "comes from a fork"),
    ({"headRefName": "-x"}, "Refusing the branch name"),
])
def test_push_fix_refuses_anything_but_your_own_open_pr_at_the_checked_commit(setup, change, text) -> None:
    cli, _repo, _head, state, pushes, args = setup
    state.update(change)
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 1 and text in result.output, result.output
    assert pushes == []
