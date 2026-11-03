"""The Linux installer: input checks run for real (they stop before any download)."""

import os
import re
import shutil
import subprocess
from pathlib import Path

INSTALLER = Path(__file__).parent.parent / "installer" / "linux" / "install-proofmerge.sh"
SH = shutil.which("sh") or "/bin/sh"  # absolute, so a test can give the script its own PATH


def run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PROOFMERGE_NO_OPEN": "1"}
    return subprocess.run(
        [SH, str(INSTALLER)],
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
    for name in ('we"ird', "100%good", "a$b"):
        result = run(tmp_path, XDG_DATA_HOME=str(tmp_path / name))
        assert result.returncode == 1 and "quote" in result.stderr
        assert not (tmp_path / name).exists()


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


def test_python_without_venv_is_skipped_for_one_that_has_it(tmp_path: Path) -> None:
    real = shutil.which("python3")
    assert real
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # python3.13 here is a Python without venv support (as on a bare Debian); python3 has it.
    no_venv = f'#!/bin/sh\ncase "$*" in *ensurepip*) exit 1;; esac\nexec {real} "$@"\n'
    for name, body in (("python3.13", no_venv), ("python3", f'#!/bin/sh\nexec {real} "$@"\n')):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (bin_dir / "grep").symlink_to(shutil.which("grep"))
    # No git on this PATH, so the script stops at step 2, after choosing a Python.
    result = run(tmp_path, PATH=str(bin_dir))
    assert result.returncode == 1 and "Git" in result.stderr
    assert "Found Python 3." in result.stdout and "venv module is missing" not in result.stderr


def test_uninstaller_asks_first_and_keeps_the_checks(tmp_path: Path) -> None:
    script = INSTALLER.read_text()
    block = 'cat > "$INSTALL_DIR/uninstall.sh" <<EOF' + script.split('uninstall.sh" <<EOF', 1)[1]
    block = block.split("\nEOF\n", 1)[0] + "\nEOF\n"
    data = tmp_path / "data"
    write = (
        f'INSTALL_DIR="{data}/proofmerge"; VENV="$INSTALL_DIR/venv"; APPS="{data}/applications"\n'
    )
    checks = tmp_path / "ProofMerge" / "runs"
    for answer, removed in (("\n\n", False), ("n\n\n", False), ("y\n\n", True)):
        for folder in (data / "proofmerge" / "venv", data / "applications", checks):
            folder.mkdir(parents=True, exist_ok=True)
        (data / "applications" / "proofmerge.desktop").write_text("")
        subprocess.run([SH, "-c", write + block], check=True)
        result = subprocess.run(
            [SH, str(data / "proofmerge" / "uninstall.sh")],
            input=answer,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert (data / "proofmerge").exists() is not removed
        assert (data / "applications" / "proofmerge.desktop").exists() is not removed
        assert checks.exists()
