"""Build a reproducible synthetic Go git repo for the multi-language demo."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEMO_ROOT = Path(__file__).resolve().parent
DEFAULT_TARGET = DEMO_ROOT / ".build" / "go-shop"
CANDIDATES_DIR = DEMO_ROOT / "candidates-go"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Demo Builder",
    "GIT_AUTHOR_EMAIL": "builder@example.com",
    "GIT_COMMITTER_NAME": "Demo Builder",
    "GIT_COMMITTER_EMAIL": "builder@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}

GO_MOD = """module goshop

go 1.23
"""

PRICING_GO = """package pricing

import "fmt"

var prices = map[string]float64{
	"widget": 25.0,
	"gadget": 40.0,
}

func PriceFor(tenant, sku string) (float64, error) {
	if _, ok := prices[sku]; !ok {
		return 0, fmt.Errorf("unknown sku: %s", sku)
	}
	if tenant != "tenant-a" && tenant != "tenant-b" {
		return 0, fmt.Errorf("unknown tenant: %s", tenant)
	}
	return prices[sku], nil
}
"""

# The unknown-tenant test runs first, before any price is cached, so the candidate
# still passes the existing suite: the cache bug is the regression the suite misses.
PRICING_TEST_GO = """package pricing

import "testing"

func TestPriceForUnknownTenant(t *testing.T) {
	_, err := PriceFor("tenant-x", "widget")
	if err == nil {
		t.Fatal("expected error for unknown tenant")
	}
}

func TestPriceForKnown(t *testing.T) {
	for _, tc := range []struct {
		tenant string
		sku    string
		want   float64
	}{
		{"tenant-a", "widget", 25.0},
		{"tenant-b", "widget", 25.0},
		{"tenant-a", "gadget", 40.0},
	} {
		got, err := PriceFor(tc.tenant, tc.sku)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != tc.want {
			t.Fatalf("PriceFor(%q, %q) = %v, want %v", tc.tenant, tc.sku, got, tc.want)
		}
	}
}
"""

PROOFMERGE_TOML = """language = "go"
image = "golang:1.23-alpine"
probe_path = "pricing/proofmerge_probe_test.go"
test_command = ["go", "test", "-count=1", "-p=1", "-run", "TestProofMergeProbe", "./pricing"]
assertion_failure = "FAIL: TestProofMergeProbe"

[env]
GOCACHE = "/tmp/gocache"
HOME = "/tmp"
GOFLAGS = "-buildvcs=false"
"""

CANDIDATE_FILES = {
    "pricing/pricing.go": """package pricing

import "fmt"

var prices = map[string]float64{
	"widget": 25.0,
	"gadget": 40.0,
}

var cache = map[string]float64{}

func PriceFor(tenant, sku string) (float64, error) {
	if price, ok := cache[sku]; ok {
		return price, nil
	}
	if _, ok := prices[sku]; !ok {
		return 0, fmt.Errorf("unknown sku: %s", sku)
	}
	if tenant != "tenant-a" && tenant != "tenant-b" {
		return 0, fmt.Errorf("unknown tenant: %s", tenant)
	}
	price := prices[sku]
	cache[sku] = price
	return price, nil
}
""",
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


def write_files(repo_dir: Path, files: dict[str, str]) -> None:
    """Write a relative-path -> content map into repo_dir."""
    for rel, content in files.items():
        target = repo_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def commit_all(repo_dir: Path, message: str) -> None:
    """Stage and commit all changes in repo_dir."""
    run_git(repo_dir, "add", "-A")
    run_git(repo_dir, "commit", "-m", message)


def build_repo(target_dir: Path, force: bool) -> None:
    """Create the Go demo repository with base and candidate branches."""
    if target_dir.exists():
        if not force:
            print(f"Refusing to overwrite existing target: {target_dir}")
            sys.exit(1)
        if target_dir.is_dir():
            shutil.rmtree(target_dir)
        else:
            target_dir.unlink()

    target_dir.mkdir(parents=True)

    run_git(target_dir, "init", "--initial-branch=main")
    run_git(target_dir, "config", "user.name", GIT_ENV["GIT_AUTHOR_NAME"])
    run_git(target_dir, "config", "user.email", GIT_ENV["GIT_AUTHOR_EMAIL"])

    write_files(
        target_dir,
        {
            "go.mod": GO_MOD,
            "pricing/pricing.go": PRICING_GO,
            "pricing/pricing_test.go": PRICING_TEST_GO,
            ".proofmerge.toml": PROOFMERGE_TOML,
        },
    )
    commit_all(target_dir, "Initial goshop base")

    intent_file = CANDIDATES_DIR / "go-tenant-cache" / "intent.md"
    intent = intent_file.read_text().strip()
    message = intent.splitlines()[0].strip()

    run_git(target_dir, "checkout", "-b", "candidate/go-tenant-cache", "main")
    write_files(target_dir, CANDIDATE_FILES)
    commit_all(target_dir, message)
    run_git(target_dir, "checkout", "main")

    print(f"Go demo repo created at: {target_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the synthetic Go demo repository."
    )
    parser.add_argument(
        "target_dir",
        nargs="?",
        default=str(DEFAULT_TARGET),
        help="Target directory for the generated repo",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite target directory if it exists",
    )
    args = parser.parse_args(argv)
    build_repo(Path(args.target_dir), args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
