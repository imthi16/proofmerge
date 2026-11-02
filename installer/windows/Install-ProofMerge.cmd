<# : This first block is the batch launcher; PowerShell reads it as a comment.
@echo off
setlocal
title Install ProofMerge
set "PM_SELF=%~f0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Expression ([IO.File]::ReadAllText($env:PM_SELF))"
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
# The ref goes into a pip requirement; allow only branch, tag and commit characters.
if ($Ref -notmatch '\A[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\z' -or $Ref -match '\.\.') {
    Write-Host "    PROOFMERGE_REF '$Ref' is not a branch, tag or commit name." -ForegroundColor Red
    exit 1
}
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
    # Callers look for the tool again instead of trusting this code: winget also returns
    # non-zero for "already installed" and "restart needed".
    return ($code -eq 0)
}

function Stop-App {
    # A running app locks proofmerge-app.exe, so pip cannot replace it. Stop only processes
    # started from this venv (the launcher and its Python), never another copy on the machine,
    # and wait until they have exited.
    $ErrorActionPreference = 'Continue'
    $mine = @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Venv + '\', [StringComparison]::OrdinalIgnoreCase) })
    if ($mine.Count) {
        $mine | Stop-Process -Force -ErrorAction SilentlyContinue
        $mine | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue
    }
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
    $link.WorkingDirectory = $env:TEMP  # never the install folder, so the uninstaller can delete it
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
        if (Ask 'Install Python 3.13 now?') {
            Install-WithWinget 'Python.Python.3.13' 'Python 3.13' | Out-Null
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
        if (Ask 'Install Git now?') { Install-WithWinget 'Git.Git' 'Git' | Out-Null }
        if (-not (Has 'git')) {
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
    Stop-App
    Say "Downloading ProofMerge ($Ref) from $Repo. This takes a minute."
    # Same version number, new code: pip keeps an installed copy unless forced. Forcing only
    # this package replaces the old copy after the new one has downloaded and built, so a
    # failed download leaves the working install in place. The second call adds any new
    # dependencies.
    & $VenvPython -m pip install --disable-pip-version-check --force-reinstall --no-deps "proofmerge @ git+$Repo@$Ref"
    if ($LASTEXITCODE -eq 0) { & $VenvPython -m pip install --disable-pip-version-check "proofmerge @ git+$Repo@$Ref" }
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
    # Run by the uninstaller: stop only this install's processes (they lock the files), wait,
    # then remove the shortcuts from folders looked up at that moment.
    $UnlinkCommand = @'
$own = (Join-Path $env:LOCALAPPDATA 'ProofMerge') + '\'; Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($own, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -PassThru -ErrorAction SilentlyContinue | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue; foreach ($f in 'Programs','Desktop') { Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath($f)) 'ProofMerge.lnk') -ErrorAction SilentlyContinue }; Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Programs')) 'Uninstall ProofMerge.lnk') -ErrorAction SilentlyContinue
'@
    # The uninstaller holds no paths of its own: folders are looked up when it runs, so a
    # non-ASCII user or desktop folder cannot be garbled by the batch file's code page.
    $lines = @(
        '@echo off',
        'title Uninstall ProofMerge',
        'cd /d "%TEMP%"',
        'echo Removing ProofMerge. Your checks in %USERPROFILE%\ProofMerge stay where they are.',
        ('"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "' + $UnlinkCommand + '"'),
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
    Start-Process -FilePath $AppExe -WorkingDirectory $env:TEMP
    Ok 'ProofMerge is opening in your browser. Its small window must stay open while you use it.'
    Say 'Next time, open it from the Start menu or the desktop.'
    exit 0
} catch {
    Write-Host ''
    Write-Host "    $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
