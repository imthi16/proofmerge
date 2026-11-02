"""The Windows installer cannot run here, so these checks pin what it must and must not do."""

from pathlib import Path

INSTALLER = Path(__file__).parent.parent / "installer" / "windows" / "Install-ProofMerge.cmd"


def text() -> str:
    return INSTALLER.read_bytes().decode("ascii")  # Windows PowerShell 5.1 reads ASCII safely


def test_one_double_clickable_file_with_windows_line_endings() -> None:
    raw = INSTALLER.read_bytes()
    assert raw.startswith(b"<# :")  # batch label line that PowerShell reads as a comment
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    batch = text().split("#>", 1)[0]
    # The batch part hands its own path over in an environment variable, so a quote in
    # the folder name cannot break the PowerShell command line.
    assert 'set "PM_SELF=%~f0"' in batch and "$env:PM_SELF" in batch
    assert "ReadAllText" in batch and "pause" in batch


def test_installs_proofmerge_for_the_user_without_touching_path() -> None:
    script = text()
    assert "$env:LOCALAPPDATA 'ProofMerge'" in script
    assert '"proofmerge @ git+$Repo@$Ref"' in script and "https://github.com/imthi16/proofmerge" in script
    assert "proofmerge-app.exe" in script
    assert "SetEnvironmentVariable" not in script  # PATH is read, never written


def test_never_accepts_licenses_or_runs_downloaded_scripts() -> None:
    script = text()
    assert "--accept-package-agreements" not in script
    assert "--accept-source-agreements" not in script
    for remote_exec in ("Invoke-WebRequest", "irm ", "iwr ", "DownloadString", "| iex"):
        assert remote_exec not in script
    # Each of the three winget installs (Python, Git, Docker Desktop) sits behind a question.
    assert script.count("Install-WithWinget '") == 3 and script.count("(Ask '") == 3


def test_git_stores_the_file_as_is_so_the_raw_download_keeps_crlf() -> None:
    attributes = (INSTALLER.parents[2] / ".gitattributes").read_text()
    assert "*.cmd -text" in attributes


def test_system_programs_are_called_by_full_path() -> None:
    # cmd looks in the current folder (often Downloads) before PATH.
    script = text()
    assert '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile' in script
    assert "\npowershell " not in script and "\r\npowershell " not in script


def test_rerun_replaces_the_code_even_when_the_version_number_is_the_same() -> None:
    script = text()
    stop, uninstall, install = (script.index(s) for s in (
        "    Stop-App\r\n", "'pip', 'uninstall'", 'pip install --disable-pip-version-check "proofmerge @'))
    assert stop < uninstall < install
    assert "--quiet --disable-pip-version-check --upgrade" not in script  # progress stays visible


def test_ref_is_checked_before_it_reaches_pip() -> None:
    script = text()
    assert script.index("$Ref -notmatch") < script.index("git+$Repo@$Ref")


def test_uninstaller_keeps_the_users_checks_and_can_delete_its_folder() -> None:
    script = text()
    uninstall = script[script.index("$lines = @("):script.index("Set-Content")]
    assert 'rmdir /s /q "%LOCALAPPDATA%\\ProofMerge' in uninstall
    assert "USERPROFILE%\\ProofMerge stay" in uninstall
    assert "rmdir /s /q \"%USERPROFILE%" not in uninstall
    # Leaves the folder it deletes, stops the app that locks it, and looks folders up at run
    # time instead of baking in paths that an ASCII file could garble.
    assert uninstall.index("cd /d \"%TEMP%\"") < uninstall.index("taskkill.exe") < uninstall.index("rmdir")
    assert "GetFolderPath" in uninstall and "$Desktop" not in uninstall
    assert "$link.WorkingDirectory = $env:TEMP" in script
