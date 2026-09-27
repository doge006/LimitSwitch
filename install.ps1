# Installs (or updates) Account Switcher for this Windows user, from the latest GitHub release:
#   irm https://raw.githubusercontent.com/doge006/Account-Switcher/main/install.ps1 | iex
# It asks where to install (suggesting %LOCALAPPDATA%\Programs\Account Switcher, or where you
# installed it last time), downloads the portable zip there, replaces an older copy (your accounts
# and settings are kept elsewhere) and starts it. The app adds itself to the Start menu.
# -Dir installs there without asking; -Zip installs a local zip; -NoStart doesn't start it.
param([string]$Zip = '', [string]$Dir = '', [switch]$NoStart)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is far faster without its progress bar
$data = Join-Path $env:LOCALAPPDATA 'AccountSwitcher'
$remembered = Join-Path $data 'install-folder.txt'

function Test-Writable($folder) {
    try {
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $probe = Join-Path $folder ('.write-test-' + [guid]::NewGuid())
        Set-Content -LiteralPath $probe -Value 'ok'
        Remove-Item -LiteralPath $probe
        return $true
    } catch { return $false }
}

if (-not $Dir) {
    $suggested = Join-Path $env:LOCALAPPDATA 'Programs\Account Switcher'
    if (Test-Path -LiteralPath $remembered) {
        $last = (Get-Content -LiteralPath $remembered -Raw).Trim()
        if ($last) { $suggested = $last }
    }
    while ($true) {
        Write-Host ''
        Write-Host 'Where should Account Switcher be installed?'
        $answer = Read-Host "Folder (press Enter for $suggested)"
        $answer = [Environment]::ExpandEnvironmentVariables($answer.Trim().Trim('"').Trim("'"))
        $Dir = if ($answer) { $answer } else { $suggested }
        $Dir = [IO.Path]::GetFullPath($Dir)
        if (Test-Writable $Dir) { break }
        Write-Host "Can't write to $Dir (a folder like Program Files needs admin rights). Pick another folder." -ForegroundColor Yellow
    }
}
$temp = Join-Path $env:TEMP ('AccountSwitcher-install-' + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $temp | Out-Null
try {
    if (-not $Zip) {
        Write-Host 'Finding the latest Account Switcher...'
        $release = Invoke-RestMethod 'https://api.github.com/repos/doge006/Account-Switcher/releases/latest' `
            -Headers @{ 'User-Agent' = 'AccountSwitcher-installer' }
        $asset = $release.assets | Where-Object { $_.name -eq 'AccountSwitcher-windows.zip' } | Select-Object -First 1
        if (-not $asset) { throw "The latest release ($($release.tag_name)) has no Windows download." }
        $Zip = Join-Path $temp 'AccountSwitcher-windows.zip'
        Write-Host "Downloading $($release.tag_name)..."
        Invoke-WebRequest $asset.browser_download_url -OutFile $Zip
    }
    Expand-Archive -LiteralPath $Zip -DestinationPath (Join-Path $temp 'unpacked')
    $source = Join-Path $temp 'unpacked\Account Switcher'
    if (-not (Test-Path -LiteralPath $source)) { $source = Join-Path $temp 'unpacked' }

    # A running copy from this folder closes first (its files are replaced).
    $running = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Dir) }
    if ($running) {
        Write-Host 'Closing the running Account Switcher...'
        $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    robocopy $source $Dir /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the files failed (robocopy $LASTEXITCODE)." }
    Write-Host "Installed in $Dir"
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    Set-Content -LiteralPath $remembered -Value $Dir  # suggested next time (updates)
    if (-not $NoStart) {
        Start-Process -FilePath (Join-Path $Dir 'Account Switcher.exe')
        Write-Host 'Started. Account Switcher is in the tray (and in the Start menu from now on).'
    }
} finally {
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
