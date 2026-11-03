"""The Linux installer: input checks run for real (they stop before any download)."""

import os
import re
import subprocess
from pathlib import Path

INSTALLER = Path(__file__).parent.parent / "installer" / "linux" / "install-proofmerge.sh"


def run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PROOFMERGE_NO_OPEN": "1"}
    return subprocess.run(
        ["sh", str(INSTALLER)],
        env={**base, **env},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_bad_ref_stops_before_anything_is_installed(tmp_path: Path) -> None:
    for ref in ("main..evil", "-rf", "main;rm", "a b"):
        result = run(tmp_path, PROOFMERGE_REF=ref)
        assert result.returncode == 1 and "is not a branch" in result.stderr
    assert not (tmp_path / ".local").exists()


def test_install_folder_with_shell_characters_is_refused(tmp_path: Path) -> None:
    result = run(tmp_path, XDG_DATA_HOME=str(tmp_path / 'we"ird'))
    assert result.returncode == 1 and "quote" in result.stderr
    assert not (tmp_path / 'we"ird').exists()


def test_never_runs_sudo_or_downloaded_scripts() -> None:
    script = INSTALLER.read_text()
    # sudo appears only inside text printed for the user, never as a command.
    for line in script.splitlines():
        code = line.split("#", 1)[0]
        assert not re.match(r"\s*sudo\b", code)
        assert "| sh" not in code and "| bash" not in code
    assert "curl" not in script and "wget" not in script


def test_installs_for_the_user_and_keeps_the_checks_on_uninstall() -> None:
    script = INSTALLER.read_text()
    assert "VENV=$INSTALL_DIR/venv" in script and "${XDG_DATA_HOME:-$HOME/.local/share}" in script
    assert '"proofmerge @ git+$REPO@$REF"' in script and "--force-reinstall --no-deps" in script
    uninstall = script.split('uninstall.sh" <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert 'rm -rf "$INSTALL_DIR"' in uninstall
    assert not re.search(r"rm [^\n]*ProofMerge", uninstall)  # ~/ProofMerge (the checks) stays
