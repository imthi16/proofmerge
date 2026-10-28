"""Build a reproducible synthetic git repo for the PR verifier demo."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
BASE_SOURCE = DEMO_ROOT / "demo-shop"
DEFAULT_CANDIDATES_DIR = DEMO_ROOT / "candidates"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Demo Builder",
    "GIT_AUTHOR_EMAIL": "builder@example.com",
    "GIT_COMMITTER_NAME": "Demo Builder",
    "GIT_COMMITTER_EMAIL": "builder@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def run_git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command with deterministic environment."""
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


def copy_tree(src: Path, dst: Path) -> None:
    """Copy all files from src to dst, preserving relative structure."""
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # copyfile, not copy2: a copied old mtime with an equal size lets git's
            # stat cache treat a changed file as unchanged, silently dropping it.
            shutil.copyfile(item, target)


def commit_all(repo_dir: Path, message: str) -> None:
    """Stage and commit all changes in repo_dir."""
    run_git(repo_dir, "add", "-A")
    run_git(repo_dir, "commit", "-m", message)


def assert_committed(repo_dir: Path, files_dir: Path) -> None:
    """Fail loudly if any overlay file is missing from, or differs in, HEAD."""
    for item in files_dir.rglob("*"):
        if item.is_file():
            rel = item.relative_to(files_dir).as_posix()
            committed = run_git(repo_dir, "show", f"HEAD:{rel}", check=False).stdout
            if committed != item.read_text():
                raise SystemExit(f"Build error: {rel} was not committed as written")


def build_repo(target_dir: Path, force: bool, candidates_dir: Path) -> None:
    """Create the demo repository with base and candidate branches."""
    if target_dir.exists():
        if not force:
            print(f"Refusing to overwrite existing target: {target_dir}")
            sys.exit(1)
        if target_dir.is_dir():
            shutil.rmtree(target_dir)
        else:
            target_dir.unlink()

    target_dir.mkdir(parents=True)

    # Initialize repo on main with base source.
    run_git(target_dir, "init", "--initial-branch=main")
    run_git(target_dir, "config", "user.name", GIT_ENV["GIT_AUTHOR_NAME"])
    run_git(target_dir, "config", "user.email", GIT_ENV["GIT_AUTHOR_EMAIL"])
    copy_tree(BASE_SOURCE, target_dir)
    commit_all(target_dir, "Initial demo-shop base")

    # Create candidate branches.
    for candidate_dir in sorted(candidates_dir.iterdir()):
        if not candidate_dir.is_dir():
            continue
        name = candidate_dir.name
        intent_file = candidate_dir / "intent.md"
        files_dir = candidate_dir / "files"
        intent = intent_file.read_text().strip()
        message = intent.splitlines()[0].strip()

        run_git(target_dir, "checkout", "-b", f"candidate/{name}", "main")
        copy_tree(files_dir, target_dir)
        commit_all(target_dir, message)
        assert_committed(target_dir, files_dir)
        run_git(target_dir, "checkout", "main")

    print(f"Demo repo created at: {target_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the synthetic demo-shop git repository."
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=str(DEMO_ROOT / ".build" / "demo-shop"),
        help="Target directory for the generated repo",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite target directory if it exists",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=DEFAULT_CANDIDATES_DIR,
        help="Directory containing candidate overlay directories",
    )
    args = parser.parse_args(argv)
    build_repo(Path(args.target_dir), args.force, args.candidates.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
