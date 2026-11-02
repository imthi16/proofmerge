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


def test_uninstaller_keeps_the_users_checks() -> None:
    script = text()
    uninstall = script[script.index("$lines = @("):script.index("Set-Content")]
    assert 'rmdir /s /q "%LOCALAPPDATA%\\ProofMerge' in uninstall
    assert "USERPROFILE%\\ProofMerge stay" in uninstall
    assert "rmdir /s /q \"%USERPROFILE%" not in uninstall
