"""Build a reproducible synthetic TypeScript git repo for the Node auto-setup demo.

The repo has no `.proofmerge.toml`: ProofMerge sets it up from package.json and
package-lock.json. Its own vitest suite passes on main and on the candidate branch,
which still changes what `reimbursableCents` returns.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
SOURCE = DEMO_ROOT / "ts-shop"
DEFAULT_TARGET = DEMO_ROOT / ".build" / "ts-shop"
CANDIDATE = "expense-totals-refactor"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Demo Builder",
    "GIT_AUTHOR_EMAIL": "builder@example.com",
    "GIT_COMMITTER_NAME": "Demo Builder",
    "GIT_COMMITTER_EMAIL": "builder@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV})


def build(target: Path, force: bool) -> None:
    if target.exists():
        if not force:
            raise SystemExit(f"{target} exists; pass --force to rebuild it")
        shutil.rmtree(target)
    shutil.copytree(SOURCE / "base", target)
    git(target, "init", "--initial-branch=main")
    git(target, "add", "-A")
    git(target, "commit", "-m", "Expense totals with per-item category caps")
    git(target, "checkout", "-b", f"candidate/{CANDIDATE}")
    candidate = SOURCE / "candidates" / CANDIDATE
    shutil.copytree(candidate / "src", target / "src", dirs_exist_ok=True)
    git(target, "add", "-A")
    git(target, "commit", "-m", "Refactor reimbursableCents for readability")
    git(target, "checkout", "main")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    build(args.target, args.force)
    print(f"built {args.target}: main and candidate/{CANDIDATE}")
    print(f"intent: {SOURCE / 'candidates' / CANDIDATE / 'intent.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
