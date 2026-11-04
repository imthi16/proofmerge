import json
import re
import tomllib
from pathlib import Path

import typer

from proofmerge.cli import app
from proofmerge.hypotheses import MAX_HYPOTHESES
from proofmerge.probes import MAX_BASE_RUNS

SKILL = Path(__file__).parent.parent / "skills" / "proofmerge" / "SKILL.md"


def test_frontmatter_names_the_skill_folder() -> None:
    frontmatter = SKILL.read_text().split("---")[1]
    fields = dict(line.split(": ", 1) for line in frontmatter.strip().splitlines())

    assert fields["name"] == SKILL.parent.name
    assert 0 < len(fields["description"]) <= 1024


def test_commands_and_options_it_names_exist() -> None:
    text = SKILL.read_text()
    commands = typer.main.get_command(app).commands

    for name in set(re.findall(r"proofmerge (\w+)", text)) - {"found"}:
        assert name in commands, name
    options = {"--help"} | {opt for cmd in commands.values() for p in cmd.params for opt in p.opts}
    for option in set(re.findall(r"(--[a-z][a-z-]+)", text)):
        assert option in options, option


def test_call_budget_matches_verify() -> None:
    # verify() caps the client at 4 + MAX_HYPOTHESES * MAX_BASE_RUNS calls.
    assert f"at most {4 + MAX_HYPOTHESES * MAX_BASE_RUNS} Meta model calls" in SKILL.read_text()


ROOT = SKILL.parents[2]


def manifest(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def test_claude_and_codex_plugins_ship_this_skill_at_the_package_version() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    claude, codex = manifest(".claude-plugin/plugin.json"), manifest(".codex-plugin/plugin.json")
    for plugin in (claude, codex):
        assert plugin["name"] == SKILL.parent.name and plugin["version"] == version
    assert (ROOT / codex["skills"] / SKILL.parent.name / "SKILL.md") == SKILL
    assert (ROOT / claude["icon"]).is_file()

    # The repo is its own marketplace for all three: one entry, pointing at the repo root.
    # Muse Code reads .agents/ first and skips a "url" source, so the root is a "local" path (Codex takes both).
    claude_market, codex_market = manifest(".claude-plugin/marketplace.json"), manifest(".agents/plugins/marketplace.json")
    assert [p["name"] for p in claude_market["plugins"]] == [SKILL.parent.name]
    assert claude_market["plugins"][0]["source"] == "./"
    assert [p["name"] for p in codex_market["plugins"]] == [SKILL.parent.name]
    assert codex_market["plugins"][0]["source"] == {"source": "local", "path": "./"}


def test_skill_puts_the_hard_rules_before_the_steps() -> None:
    text = SKILL.read_text()
    rules = text.index("## Rules that always apply")
    assert rules < text.index("## 1. Check that it can run") < text.index("## 3. Run it")
    for rule in ("NEVER edit, skip, weaken or delete tests", "NEVER edit files in an evidence directory",
                 "NEVER ask the user to paste `MODEL_API_KEY`"):
        assert rules < text.index(rule) < text.index("## 1.")
