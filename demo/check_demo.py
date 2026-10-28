"""Validate the demo repo branches against the ground-truth expectations."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
DEFAULT_GROUND_TRUTH_DIR = DEMO_ROOT / "ground_truth"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Demo Builder",
    "GIT_AUTHOR_EMAIL": "builder@example.com",
    "GIT_COMMITTER_NAME": "Demo Builder",
    "GIT_COMMITTER_EMAIL": "builder@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}

def run_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command with deterministic environment."""
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def run_tests(repo_dir: Path, *test_paths: str) -> subprocess.CompletedProcess:
    """Run pytest inside repo_dir with PYTHONPATH set to its src layout."""
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_dir / "src"),
        # Branch switches can rewrite a same-size source file within the same second;
        # a cached .pyc would then run the previous branch's code.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_paths]
    return subprocess.run(
        argv,
        cwd=repo_dir,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def result_label(returncode: int) -> str:
    """Map pytest exit code to PASS/FAIL/ERROR."""
    # Only pytest exit 1 is a test failure; anything else (usage error, no tests,
    # killed by a signal) is an execution error, never evidence.
    if returncode == 0:
        return "PASS"
    if returncode == 1:
        return "FAIL"
    return "ERROR"


def list_candidate_branches(repo_dir: Path) -> list[str]:
    result = run_git(repo_dir, "branch", "--list", "candidate/*")
    return [line.strip().lstrip("* ") for line in result.stdout.splitlines()]


def run_base_tests_on_head(repo_dir: Path) -> subprocess.CompletedProcess | None:
    """Run main's tests/test_pricing.py against the currently checked-out code."""
    result = run_git(repo_dir, "show", "main:tests/test_pricing.py")
    if result.returncode != 0:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        base_test = tmp_path / "test_pricing.py"
        base_test.write_text(result.stdout)
        return run_tests(repo_dir, str(base_test))


def check_repo(repo_dir: Path, ground_truth_dir: Path) -> int:
    repo_dir = repo_dir.resolve()
    if not repo_dir.exists():
        print(f"Repo directory does not exist: {repo_dir}")
        return 1

    labels_file = ground_truth_dir / "labels.json"
    labels = json.loads(labels_file.read_text())
    branches = ["main"] + sorted(list_candidate_branches(repo_dir))
    missing = {f"candidate/{name}" for name in labels} - set(branches)
    if missing:
        print(f"Labeled candidate branches missing from repo: {sorted(missing)}")
        return 1

    rows: list[tuple[str, str, str, str]] = []
    all_ok = True
    any_error = False

    for branch in branches:
        if run_git(repo_dir, "checkout", branch).returncode != 0:
            print(f"Could not check out {branch}; refusing to test the wrong code")
            return 1

        existing = run_tests(repo_dir, str(repo_dir / "tests"))
        existing_label = result_label(existing.returncode)
        if existing.returncode not in (0, 1):
            any_error = True
        elif existing.returncode != 0:
            all_ok = False

        candidate_name = branch.split("/", 1)[1] if branch.startswith("candidate/") else None
        gt_dir = ground_truth_dir / candidate_name if candidate_name else None
        if branch == "main":
            # Only BLOCK ground-truth probes must pass against the clean base.
            # NOT_BLOCK probes may encode new behavior that does not exist on main.
            block_candidates = {
                name for name, val in labels.items()
                if (val["verdict"] if isinstance(val, dict) else val) == "BLOCK"
            }
            gt_tests = sorted(
                p
                for name in block_candidates
                for p in (ground_truth_dir / name).glob("test_*.py")
                if (ground_truth_dir / name).exists()
            )
            if not gt_tests:
                gt_label = "ERROR"
                any_error = True
            else:
                gt = run_tests(repo_dir, *[str(p) for p in gt_tests])
                gt_label = result_label(gt.returncode)
                if gt.returncode not in (0, 1):
                    any_error = True
                elif gt.returncode != 0:
                    all_ok = False
        elif candidate_name and candidate_name in labels:
            expected = labels[candidate_name]
            verdict = expected["verdict"] if isinstance(expected, dict) else expected
            if verdict == "BLOCK" and (not gt_dir or not gt_dir.exists()):
                gt_label = "ERROR"
                any_error = True
            elif verdict == "BLOCK" and gt_dir and gt_dir.exists():
                gt_tests = sorted(gt_dir.glob("test_*.py"))
                if not gt_tests:
                    gt_label = "ERROR"
                    any_error = True
                else:
                    gt = run_tests(repo_dir, *[str(p) for p in gt_tests])
                    gt_label = result_label(gt.returncode)
                    if gt.returncode not in (0, 1):
                        any_error = True
                    elif gt.returncode == 0:
                        # BLOCK candidate ground-truth should fail.
                        all_ok = False
            elif verdict == "NOT_BLOCK" and gt_dir and gt_dir.exists():
                gt_tests = sorted(gt_dir.glob("test_*.py"))
                gt = run_tests(repo_dir, *[str(p) for p in gt_tests]) if gt_tests else None
                gt_label = result_label(gt.returncode) if gt else "n/a"
                if gt and gt.returncode not in (0, 1):
                    any_error = True
                elif gt and gt.returncode != 0:
                    # NOT_BLOCK candidate ground-truth should pass.
                    all_ok = False
            else:
                gt_label = "n/a"
        else:
            gt_label = "n/a"
            expected = None

        if branch.startswith("candidate/"):
            base_on_head = run_base_tests_on_head(repo_dir)
            base_label = result_label(base_on_head.returncode) if base_on_head else "ERROR"
            if base_on_head is None:
                any_error = True
            elif base_on_head.returncode not in (0, 1):
                any_error = True
            elif isinstance(expected, dict) and "base_on_head" in expected:
                expected_base = expected["base_on_head"]
                if expected_base == "FAIL" and base_on_head.returncode == 0:
                    all_ok = False
                elif expected_base == "PASS" and base_on_head.returncode != 0:
                    all_ok = False
        else:
            base_label = "n/a"

        rows.append((branch, existing_label, gt_label, base_label))

    print(
        f"{'branch':<35} {'existing suite':<15} "
        f"{'ground-truth test':<18} {'base tests on head':<18}"
    )
    print("-" * 86)
    for branch, existing_label, gt_label, base_label in rows:
        print(
            f"{branch:<35} {existing_label:<15} "
            f"{gt_label:<18} {base_label:<18}"
        )

    return 0 if all_ok and not any_error else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the demo repo against ground-truth expectations."
    )
    parser.add_argument(
        "repo_dir",
        nargs="?",
        default=str(DEMO_ROOT / ".build" / "demo-shop"),
        help="Path to the generated demo repo",
    )
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=DEFAULT_GROUND_TRUTH_DIR,
        help="Directory containing ground-truth tests and labels.json",
    )
    args = parser.parse_args(argv)
    return check_repo(Path(args.repo_dir), args.ground_truth.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
