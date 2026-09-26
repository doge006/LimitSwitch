<#
.SYNOPSIS
  One-shot updater: download the latest Account Switcher from GitHub into this folder and install it.

.EXAMPLE
  .\Update.cmd                                              # update the current branch
  .\Update.cmd -Branch claude/pensive-brahmagupta-cufhuf    # switch to (and update) another branch

.NOTES
  Runs once and exits; nothing is scheduled or left running.
  - Uses git. A folder downloaded as a ZIP is converted into a git checkout on first run
    (tracked files are replaced; ignored files such as the built proxy are kept).
  - Never discards local edits unless -Force is given; with -Force they are stashed or
    saved to a backup branch first.
  - Reinstalls Python requirements when they changed.
  - If Account Switcher is running, it is shut down cleanly (so the proxy and Claude
    processes it owns stop too) and started again on the new version.
#>
[CmdletBinding()]
param(
    [string]$Branch = "",
    [switch]$Force,
    [string]$Repo = "https://github.com/doge006/Account-Switcher.git"
)

# "Continue": Windows PowerShell 5.1 turns any git stderr output into a terminating
# error under "Stop". Failures are detected through exit codes in RunGit instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
# Each launcher records its private URL here while running: native.url / web.url.
$Runtime = Join-Path $Root ".runtime"

function Say([string]$Text, [string]$Color = "Gray") {
    Write-Host $Text -ForegroundColor $Color
}

function RunGit {
    # Run git in the repo; return trimmed output, throw on failure.
    $output = & git -C $Root @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed:`n$($output -join "`n")" }
    return ($output -join "`n").Trim()
}

function Find-Python {
    foreach ($name in @("python", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
    }
    $fallback = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
    if (Test-Path $fallback) { return $fallback }
    return $null
}

function Stop-RunningApp([string]$UrlFile) {
    # Returns $true if an instance was running and has been asked to exit.
    if (-not (Test-Path $UrlFile)) { return $false }
    $url = (Get-Content $UrlFile -Raw).Trim()
    if ($url -notmatch "^(http://127\.0\.0\.1:\d+)/#token=(.+)$") { return $false }
    try {
        Invoke-RestMethod -Method Post -Uri "$($Matches[1])/api/shutdown" -Body "{}" -ContentType "application/json" `
            -Headers @{ Authorization = "Bearer $($Matches[2])" } -TimeoutSec 5 -ErrorAction Stop | Out-Null
    } catch {
        Remove-Item $UrlFile -ErrorAction SilentlyContinue  # stale file from a crashed run
        return $false
    }
    Say "Closing the running Account Switcher..." Cyan
    # The app deletes its URL file once it has fully exited.
    for ($i = 0; $i -lt 60 -and (Test-Path $UrlFile); $i++) { Start-Sleep -Milliseconds 250 }
    return $true
}

function Update-Checkout {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is not installed. Install it with:  winget install --id Git.Git -e"
    }

    if (-not (Test-Path (Join-Path $Root ".git"))) {
        $target = if ($Branch) { $Branch } else { "main" }
        Say "This folder is not a git checkout yet; converting it to $Repo ($target)." Yellow
        RunGit init --quiet | Out-Null
        RunGit remote add origin $Repo | Out-Null
        RunGit fetch --quiet origin $target | Out-Null
        RunGit checkout --quiet -f -B $target "origin/$target" | Out-Null
        RunGit branch --quiet "--set-upstream-to=origin/$target" | Out-Null
        return @{ Status = "updated"; Before = $null; After = (RunGit rev-parse HEAD) }
    }

    $current = RunGit rev-parse --abbrev-ref HEAD
    $target = if ($Branch) { $Branch } elseif ($current -ne "HEAD") { $current } else { "main" }
    Say "Checking GitHub for updates to $target..." Gray
    RunGit fetch --quiet origin $target | Out-Null
    $before = RunGit rev-parse HEAD
    if ($before -eq (RunGit rev-parse "origin/$target") -and $current -eq $target) {
        return @{ Status = "current" }
    }

    if (RunGit status --porcelain --untracked-files=no) {
        if (-not $Force) {
            Say "An update is available, but you have local edits to these files:" Yellow
            Write-Host (RunGit status --short --untracked-files=no)
            Say "Commit or stash them, or rerun with -Force to back them up and update anyway." Yellow
            return @{ Status = "blocked" }
        }
        RunGit stash push --quiet -m ("update.ps1 backup " + (Get-Date -Format "yyyy-MM-dd HH:mm")) | Out-Null
        Say "Your local edits were saved with 'git stash' (see: git stash list)." Yellow
    }

    if ($current -ne $target) {
        & git -C $Root rev-parse --verify --quiet "refs/heads/$target" | Out-Null
        if ($LASTEXITCODE -eq 0) { RunGit switch --quiet $target | Out-Null }
        else { RunGit switch --quiet -c $target --track "origin/$target" | Out-Null }
        Say "Switched to branch $target." Cyan
    }

    & git -C $Root merge --quiet --ff-only "origin/$target" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if (-not $Force) {
            Say "Your branch has commits that are not on GitHub, so it can't be updated automatically." Yellow
            Say "Rerun with -Force to save them on a backup branch and take GitHub's version." Yellow
            return @{ Status = "blocked" }
        }
        $backup = "backup/update-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        RunGit branch $backup | Out-Null
        RunGit reset --quiet --hard "origin/$target" | Out-Null
        Say "Your local commits were saved on branch $backup." Yellow
    }
    return @{ Status = "updated"; Before = $before; After = (RunGit rev-parse HEAD) }
}

try {
    $result = Update-Checkout
    if ($result.Status -eq "current") { Say "Already up to date." Green; exit 0 }
    if ($result.Status -eq "blocked") { exit 1 }

    if ($result.Before -and $result.Before -ne $result.After) {
        Say "Updated $($result.Before.Substring(0,7)) -> $($result.After.Substring(0,7)):" Green
        RunGit log --oneline --no-decorate "$($result.Before)..$($result.After)" | Write-Host
        $changed = RunGit diff --name-only $result.Before $result.After
    } else {
        Say "Installed $((RunGit rev-parse --short HEAD))." Green
        $changed = "requirements-native.txt"
    }

    if ($changed -match "(^|\n)requirements-native\.txt") {
        $python = Find-Python
        if ($python) {
            Say "Installing Python requirements..." Cyan
            & $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $Root "requirements-native.txt") | Out-Host
        } else {
            Say "Python was not found; install requirements-native.txt manually." Yellow
        }
    }

    foreach ($mode in @(@{ File = "native.url"; Launcher = "Launch.cmd" }, @{ File = "web.url"; Launcher = "Launch-Web.cmd" })) {
        if (Stop-RunningApp (Join-Path $Runtime $mode.File)) {
            Start-Process -FilePath (Join-Path $Root $mode.Launcher) -WorkingDirectory $Root -WindowStyle Hidden
            Say "Restarted Account Switcher on the new version." Green
        }
    }
    Say "Done." Green
} catch {
    Say $_.Exception.Message Red
    exit 1
}
