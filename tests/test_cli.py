from typer.testing import CliRunner

from proofmerge import __version__
from proofmerge.cli import app


def test_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout == f"proofmerge {__version__}\n"
