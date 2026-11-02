"""The Windows installer cannot run here, so these checks pin what it must and must not do."""

import re
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
    # Each winget install (Python, Git, GitHub CLI, Docker Desktop) sits behind a question.
    assert script.count("Install-WithWinget '") == 4 and script.count("(Ask '") == 4


def test_git_stores_the_file_as_is_so_the_raw_download_keeps_crlf() -> None:
    attributes = (INSTALLER.parents[2] / ".gitattributes").read_text()
    assert "*.cmd -text" in attributes


def test_system_programs_are_called_by_full_path() -> None:
    # cmd looks in the current folder (often Downloads) before PATH.
    script = text()
    assert '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoProfile' in script
    assert "\npowershell " not in script and "\r\npowershell " not in script


def test_rerun_replaces_the_code_and_a_failed_download_keeps_the_old_copy() -> None:
    script = text()
    stop = script.index("    Stop-App\r\n")
    force = script.index("--force-reinstall --no-deps \"proofmerge @")
    deps = script.index('if ($LASTEXITCODE -eq 0) { & $VenvPython -m pip install --disable-pip-version-check "proofmerge @')
    assert stop < force < deps
    # Uninstalling first would leave no app when the download fails.
    assert "'pip', 'uninstall'" not in script and "pip uninstall" not in script
    assert "--quiet --disable-pip-version-check --upgrade" not in script  # progress stays visible


def test_only_this_installs_processes_are_stopped() -> None:
    script = text()
    assert "taskkill" not in script  # `/im` would stop every proofmerge-app.exe on the machine
    assert "$_.Path.StartsWith($Venv + '\\'" in script
    assert "Wait-Process" in script


def test_ref_is_checked_before_it_reaches_pip() -> None:
    script = text()
    assert script.index("$Ref -notmatch '\\A") < script.index("git+$Repo@$Ref")
    assert "{0,199}\\z'" in script  # `$` would let a trailing newline through


def test_uninstaller_keeps_the_users_checks_and_can_delete_its_folder() -> None:
    script = text()
    uninstall = script[script.index("$lines = @("):script.index("Set-Content")]
    assert 'rmdir /s /q "%LOCALAPPDATA%\\ProofMerge' in uninstall
    assert "USERPROFILE%\\ProofMerge stay" in uninstall
    assert "rmdir /s /q \"%USERPROFILE%" not in uninstall
    # Leaves the folder it deletes, stops the app that locks it, and looks folders up at run
    # time instead of baking in paths that an ASCII file could garble.
    assert uninstall.index("cd /d \"%TEMP%\"") < uninstall.index("$UnlinkCommand") < uninstall.index("rmdir")
    unlink = script[script.index("$UnlinkCommand = @'"):script.index("$lines = @(")]
    assert "Stop-Process" in unlink and "GetFolderPath" in unlink and '"' not in unlink
    assert "$Desktop" not in uninstall
    assert "$link.WorkingDirectory = $env:TEMP" in script
    assert "Start-Process -FilePath $AppExe -WorkingDirectory $env:TEMP" in script


def test_winget_displays_output_and_returns_only_exit_status() -> None:
    function = text().split("function Install-WithWinget", 1)[1].split("function Stop-App", 1)[0]
    assert "winget install --exact --id $id | Out-Host\r\n    $code = $LASTEXITCODE" in function
    assert "return ($code -eq 0)" in function


def test_github_cli_is_offered_after_git_and_is_optional() -> None:
    script = text()
    labels = re.findall(r"Step 'Step (\d) of (\d): ([^']+)'", script)
    assert [(int(n), int(total)) for n, total, _ in labels] == [(i, 7) for i in range(1, 8)]
    assert [name for _, _, name in labels][1:4] == ["Git", "GitHub sign-in", "ProofMerge"]
    step = script.split("Step 3 of 7: GitHub sign-in", 1)[1].split("Step 4 of 7", 1)[0]
    assert "(Ask 'Install the GitHub CLI now?')" in step and "Install-WithWinget 'GitHub.cli'" in step
    assert "throw" not in step  # without it the app still checks local folders
