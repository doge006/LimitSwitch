<#
.SYNOPSIS
  Pull the latest Account Switcher from GitHub into this folder, optionally restarting the app.

.EXAMPLE
  .\Update.cmd                                  # update once (current branch)
  .\Update.cmd -Branch claude/pensive-brahmagupta-cufhuf
  .\Update.cmd -Watch -Launch native            # dev loop: poll GitHub, restart the app on new commits
  .\Update.cmd -Watch -Launch web -Simulator -Interval 30

.NOTES
  - Uses git. A folder downloaded as a ZIP is converted into a git checkout on first run
    (tracked files are replaced; ignored files such as the built proxy are kept).
  - Never discards local edits unless -Force is given; with -Force they are saved to a
    backup branch first.
  - The running app is asked to shut down cleanly over its private local URL, so the proxy
    and Claude processes it owns are stopped too. Nothing is installed or scheduled.
#>
[CmdletBinding()]
param(
    [string]$Branch = "",
    [switch]$Watch,
    [ValidateRange(10, 86400)][int]$Interval = 60,
    [ValidateSet("none", "native", "web")][string]$Launch = "none",
    [switch]$Simulator,
    [switch]$Force,
    [string]$Repo = "https://github.com/doge006/Account-Switcher.git"
)

# "Continue": Windows PowerShell 5.1 turns any git stderr output into a terminating
# error under "Stop". Failures are detected through exit codes in RunGit instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root ".runtime"
$UrlFile = Join-Path $Runtime "launch-url"
$PidFile = Join-Path $Runtime "app.pid"

function Say([string]$Text, [string]$Color = "Gray") {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Text) -ForegroundColor $Color
}

function RunGit {
    # Run git in the repo; return trimmed output, throw on failure.
    $output = & git -C $Root @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed:`n$($output -join "`n")" }
    return ($output -join "`n").Trim()
}

function Ensure-Git {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is not installed. Install it with:  winget install --id Git.Git -e"
    }
    if (Test-Path (Join-Path $Root ".git")) { return }
    Say "No .git folder here; converting this download into a git checkout of $Repo" Yellow
    RunGit init --quiet | Out-Null
    RunGit remote add origin $Repo | Out-Null
    $script:Converting = $true
}

function Resolve-Branch {
    if ($Branch) { return $Branch }
    if (-not $script:Converting) {
        $current = RunGit rev-parse --abbrev-ref HEAD
        if ($current -and $current -ne "HEAD") { return $current }
    }
    return "main"
}

function Find-Python {
    # Prefer windowless interpreters so no console window lingers.
    foreach ($name in @("pythonw", "pyw")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
    }
    $fallback = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"
    if (Test-Path $fallback) { return $fallback }
    throw "Python was not found. Install Python 3.12+ from python.org (with Tcl/Tk)."
}

function Update-Once {
    $target = Resolve-Branch
    RunGit fetch --quiet origin $target | Out-Null
    $remote = RunGit rev-parse "origin/$target"

    if ($script:Converting) {
        RunGit checkout --quiet -f -B $target "origin/$target" | Out-Null
        RunGit branch --quiet "--set-upstream-to=origin/$target" | Out-Null
        $script:Converting = $false
        Say "Checked out $target at $($remote.Substring(0,7))." Green
        return "updated"
    }

    $before = RunGit rev-parse HEAD
    $current = RunGit rev-parse --abbrev-ref HEAD
    if ($before -eq $remote -and $current -eq $target) { return "current" }

    $dirty = RunGit status --porcelain --untracked-files=no
    if ($dirty) {
        if (-not $Force) {
            Say "Update available, but you have local edits. Commit/stash them or rerun with -Force:" Yellow
            Write-Host $dirty
            return "blocked"
        }
        $backup = "backup/update-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        RunGit stash push --quiet -m "update.ps1 $backup" | Out-Null
        Say "Local edits stashed (git stash list) as '$backup'." Yellow
    }

    if ($current -ne $target) {
        $exists = & git -C $Root rev-parse --verify --quiet "refs/heads/$target"
        if ($exists) { RunGit switch --quiet $target | Out-Null }
        else { RunGit switch --quiet -c $target --track "origin/$target" | Out-Null }
        $before = RunGit rev-parse HEAD
        Say "Switched to branch $target." Cyan
    }

    & git -C $Root merge --quiet --ff-only "origin/$target" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if (-not $Force) {
            Say "Your branch has commits that are not on GitHub, so it cannot fast-forward. Rerun with -Force to back them up and take GitHub's version." Yellow
            return "blocked"
        }
        $backup = "backup/update-" + (Get-Date -Format "yyyyMMdd-HHmmss")
        RunGit branch $backup | Out-Null
        RunGit reset --quiet --hard "origin/$target" | Out-Null
        Say "Local commits saved on branch $backup; reset to origin/$target." Yellow
    }

    $after = RunGit rev-parse HEAD
    if ($after -eq $before) { return "current" }
    Say "Updated $($before.Substring(0,7)) -> $($after.Substring(0,7)):" Green
    RunGit log --oneline --no-decorate "$before..$after" | Write-Host
    $changed = RunGit diff --name-only $before $after
    if ($changed -match "(^|\n)requirements[^\n]*\.txt") {
        Say "Python requirements changed; installing." Cyan
        $py = (Find-Python) -replace "pythonw\.exe$", "python.exe" -replace "pyw(\.exe)?$", "py.exe"
        & $py -m pip install --quiet -r (Join-Path $Root "requirements-native.txt") | Out-Host
    }
    return "updated"
}

function Stop-App {
    if (Test-Path $UrlFile) {
        $url = (Get-Content $UrlFile -Raw).Trim()
        if ($url -match "^(http://127\.0\.0\.1:\d+)/#token=(.+)$") {
            try {
                Invoke-RestMethod -Method Post -Uri "$($Matches[1])/api/shutdown" -Body "{}" `
                    -ContentType "application/json" -Headers @{ Authorization = "Bearer $($Matches[2])" } -TimeoutSec 5 -ErrorAction Stop | Out-Null
                Say "Asked the running app to shut down." Cyan
            } catch { }
        }
        Remove-Item $UrlFile -ErrorAction SilentlyContinue
    }
    if (Test-Path $PidFile) {
        $id = [int](Get-Content $PidFile -Raw)
        $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
        if ($proc) {
            if (-not $proc.WaitForExit(15000)) {
                Say "App did not exit in 15 s; stopping PID $id." Yellow
                Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
}

function Start-App {
    if ($Launch -eq "none") { return }
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    $arguments = @("-m", "account_switcher.$Launch", "--url-file", "`"$UrlFile`"")
    if ($Simulator) { $arguments += "--simulator" }
    $proc = Start-Process -FilePath (Find-Python) -ArgumentList $arguments -WorkingDirectory $Root -PassThru
    Set-Content -Path $PidFile -Value $proc.Id
    Say "Started account_switcher.$Launch (PID $($proc.Id))." Green
}

function App-Running {
    if (-not (Test-Path $PidFile)) { return $false }
    return [bool](Get-Process -Id ([int](Get-Content $PidFile -Raw)) -ErrorAction SilentlyContinue)
}

Ensure-Git
$branchName = Resolve-Branch
Say "Account Switcher updater - $Root - branch $branchName" White

do {
    try {
        $result = Update-Once
        if ($result -eq "current" -and -not $saidCurrent) { Say "Up to date." DarkGray }
        $saidCurrent = ($result -eq "current")
        if ($Launch -ne "none" -and ($result -eq "updated" -or -not (App-Running))) {
            Stop-App
            Start-App
        }
    } catch {
        # Network blips and similar should not kill a watch loop.
        Say $_.Exception.Message Red
        if (-not $Watch) { exit 1 }
    }
    if ($Watch) { Start-Sleep -Seconds $Interval }
} while ($Watch)
