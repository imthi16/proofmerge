<# : This first block is the batch launcher; PowerShell reads it as a comment.
@echo off
setlocal
title Install ProofMerge
set "PM_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ([IO.File]::ReadAllText($env:PM_SELF))"
set "PM_CODE=%errorlevel%"
echo.
pause
exit /b %PM_CODE%
#>

# Install ProofMerge for the current Windows user, with no terminal knowledge needed.
# Double-click this file. Running it again updates ProofMerge.
#
# What it does, in order:
#   1. finds Python 3.11 or newer, or offers to install it with winget
#   2. finds Git, or offers to install it with winget
#   3. installs ProofMerge into %LOCALAPPDATA%\ProofMerge\venv (no admin rights, PATH untouched)
#   4. offers to install Docker Desktop, which runs the tests in a sandbox
#   5. adds ProofMerge and Uninstall ProofMerge to the Start menu, and ProofMerge to the desktop
#   6. opens ProofMerge in the browser
# Winget shows each package's license and asks you to accept it; this script never accepts for you.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repo = 'https://github.com/imthi16/proofmerge'
$Ref = if ($env:PROOFMERGE_REF) { $env:PROOFMERGE_REF } else { 'main' }
$InstallDir = Join-Path $env:LOCALAPPDATA 'ProofMerge'
$Venv = Join-Path $InstallDir 'venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$AppExe = Join-Path $Venv 'Scripts\proofmerge-app.exe'
$StartMenu = [Environment]::GetFolderPath('Programs')
$Desktop = [Environment]::GetFolderPath('Desktop')

function Step($text) { Write-Host ''; Write-Host "  $text" -ForegroundColor Cyan }
function Say($text) { Write-Host "    $text" }
function Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Ask($question) { (Read-Host "    $question [Y/n]") -notmatch '^\s*(n|no)\s*$' }

function Refresh-Path {
    # winget installs update PATH for new windows only; pick the change up here too.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Install-WithWinget($id, $name) {
    if (-not (Has 'winget')) {
        Say "Windows cannot install $name for you here (winget is missing)."
        Say "Install $name yourself, then double-click this installer again."
        return $false
    }
    Say "Installing $name. Windows may ask for permission; accept its license to continue."
    winget install --exact --id $id
    $code = $LASTEXITCODE
    Refresh-Path
    return ($code -eq 0)
}

function Find-Python {
    # The py launcher is the reliable way in; plain `python` may be the Microsoft Store stub,
    # which prints nothing useful. Parse `--version` rather than pass quoted code: Windows
    # PowerShell 5.1 drops inner double quotes when it calls a program.
    $ErrorActionPreference = 'Continue'
    foreach ($candidate in @(@('py', '-3'), @('python'))) {
        if (-not (Has $candidate[0])) { continue }
        $exe = $candidate[0]
        $pre = @($candidate | Select-Object -Skip 1)
        $text = (& $exe @pre --version 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and $text -match 'Python (\d+\.\d+)' -and [version]$Matches[1] -ge [version]'3.11') {
            return , (@($exe) + $pre)
        }
    }
    return $null
}

function Write-Shortcut($folder, $name, $target, $arguments, $description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $folder "$name.lnk"))
    $link.TargetPath = $target
    $link.Arguments = $arguments
    $link.WorkingDirectory = $InstallDir
    $link.Description = $description
    $link.Save()
}

try {
    Write-Host ''
    Write-Host '  ProofMerge installer' -ForegroundColor White
    Write-Host '  Checks a code change before you merge it, with evidence.'

    Step 'Step 1 of 6: Python'
    $python = Find-Python
    if (-not $python) {
        Say 'ProofMerge needs Python 3.11 or newer, and it is not installed.'
        if ((Ask 'Install Python 3.13 now?') -and (Install-WithWinget 'Python.Python.3.13' 'Python 3.13')) {
            $python = Find-Python
        }
    }
    if (-not $python) { throw 'Python 3.11 or newer is needed. Get it from https://www.python.org/downloads/ and run this installer again.' }
    $pyExe = $python[0]
    $pyArgs = @($python | Select-Object -Skip 1)
    Ok ('Found ' + (& $pyExe @pyArgs --version))

    Step 'Step 2 of 6: Git'
    if (-not (Has 'git')) {
        Say 'ProofMerge reads your branches with Git, and it is not installed.'
        if (-not ((Ask 'Install Git now?') -and (Install-WithWinget 'Git.Git' 'Git') -and (Has 'git'))) {
            throw 'Git is needed. Get it from https://git-scm.com/downloads and run this installer again.'
        }
    }
    Ok ('Found ' + (git --version))

    Step 'Step 3 of 6: ProofMerge'
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if (-not (Test-Path $VenvPython)) {
        Say "Making a private Python environment in $Venv"
        & $pyExe @pyArgs -m venv $Venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    Say "Downloading ProofMerge ($Ref) from $Repo. This takes a minute."
    & $VenvPython -m pip install --quiet --disable-pip-version-check --upgrade "proofmerge @ git+$Repo@$Ref"
    if ($LASTEXITCODE -ne 0) { throw 'ProofMerge could not be installed. Check the internet connection and try again.' }
    if (-not (Test-Path $AppExe)) { throw "The ProofMerge launcher is missing: $AppExe" }
    Ok ('Installed ' + (& (Join-Path $Venv 'Scripts\proofmerge.exe') version))

    Step 'Step 4 of 6: Docker Desktop'
    if (Has 'docker') {
        Ok 'Found Docker. Start Docker Desktop before your first check.'
    } else {
        Say 'ProofMerge runs the tests inside Docker, so the change never touches your files.'
        Say 'Docker Desktop is a large download and may ask you to restart Windows.'
        if (Ask 'Install Docker Desktop now?') {
            if (Install-WithWinget 'Docker.DockerDesktop' 'Docker Desktop') { Ok 'Docker Desktop installed. Open it once and let it finish starting.' }
            else { Say 'Docker Desktop was not installed. The Setup page in ProofMerge links to it.' }
        } else {
            Say 'Skipped. The Setup page in ProofMerge links to it when you are ready.'
        }
    }

    Step 'Step 5 of 6: Shortcuts'
    $uninstaller = Join-Path $InstallDir 'Uninstall ProofMerge.cmd'
    $lines = @(
        '@echo off',
        'title Uninstall ProofMerge',
        'echo Removing ProofMerge. Your checks in %USERPROFILE%\ProofMerge stay where they are.',
        'del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\ProofMerge.lnk" 2>nul',
        'del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Uninstall ProofMerge.lnk" 2>nul',
        ('del /q "' + (Join-Path $Desktop 'ProofMerge.lnk') + '" 2>nul'),
        'rmdir /s /q "%LOCALAPPDATA%\ProofMerge\venv"',
        'echo Done.',
        'pause',
        '(goto) 2>nul & rmdir /s /q "%LOCALAPPDATA%\ProofMerge"'
    )
    Set-Content -Path $uninstaller -Value $lines -Encoding ASCII
    Write-Shortcut $StartMenu 'ProofMerge' $AppExe '' 'Check a change before you merge it'
    Write-Shortcut $StartMenu 'Uninstall ProofMerge' $uninstaller '' 'Remove ProofMerge; your checks stay'
    Write-Shortcut $Desktop 'ProofMerge' $AppExe '' 'Check a change before you merge it'
    Ok 'Added ProofMerge to the Start menu and the desktop.'

    Step 'Step 6 of 6: Open ProofMerge'
    Start-Process -FilePath $AppExe -WorkingDirectory $InstallDir
    Ok 'ProofMerge is opening in your browser. Its small window must stay open while you use it.'
    Say 'Next time, open it from the Start menu or the desktop.'
    exit 0
} catch {
    Write-Host ''
    Write-Host "    $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
