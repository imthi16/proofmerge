"""Offline tests for the Go demo repository builder."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_go_demo_builder_creates_expected_files_and_branch(tmp_path):
    target = tmp_path / "go-shop"
    subprocess.run(
        [sys.executable, str(ROOT / "demo" / "build_go_demo.py"), str(target)],
        check=True,
        capture_output=True,
    )
    assert (target / "go.mod").exists()
    assert "module goshop" in (target / "go.mod").read_text()
    assert (target / "pricing" / "pricing.go").exists()
    assert (target / "pricing" / "pricing_test.go").exists()
    assert (target / ".proofmerge.toml").exists()
    toml = (target / ".proofmerge.toml").read_text()
    assert 'language = "go"' in toml
    assert 'image = "golang:1.23-alpine"' in toml
    assert 'probe_path = "pricing/proofmerge_probe_test.go"' in toml

    head = git(target, "rev-parse", "candidate/go-tenant-cache")
    assert head
    head_src = git(target, "show", "candidate/go-tenant-cache:pricing/pricing.go")
    assert "var cache" in head_src
    assert "if price, ok := cache[sku]; ok" in head_src

    main_src = git(target, "show", "main:pricing/pricing.go")
    assert "var cache" not in main_src


def test_go_demo_intent_file_exists():
    intent = ROOT / "demo" / "candidates-go" / "go-tenant-cache" / "intent.md"
    assert intent.exists()
    assert "caching" in intent.read_text().lower()
