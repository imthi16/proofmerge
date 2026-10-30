import re
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
