# Adds "LimitSwitcher" to the Start menu (and, with -Desktop, the desktop), with the app icon.
# Run again at any time; it overwrites the shortcut. -Remove deletes it.
param([switch]$Desktop, [switch]$Remove, [string]$Python = "")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$places = @([Environment]::GetFolderPath('Programs'))
if ($Desktop) { $places += [Environment]::GetFolderPath('Desktop') }

if ($Remove) {
    foreach ($place in $places) { Remove-Item -LiteralPath (Join-Path $place 'LimitSwitcher.lnk') -ErrorAction SilentlyContinue }
    Write-Host 'Shortcut removed.'
    exit 0
}

# Windowless Python, so no console window flashes up: the installer's .venv first.
$pythonw = $Python
if (-not $pythonw) { $pythonw = Join-Path $root '.venv\Scripts\pythonw.exe' }
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe' }
if (-not (Test-Path -LiteralPath $pythonw)) {
    $found = Get-Command pythonw.exe, pyw.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { throw 'pythonw.exe not found. Run Install.cmd first.' }
    $pythonw = $found.Source
}

# The app's shortcut before it was renamed LimitSwitcher.
foreach ($place in $places) { Remove-Item -LiteralPath (Join-Path $place 'Account Switcher.lnk') -ErrorAction SilentlyContinue }
$shell = New-Object -ComObject WScript.Shell
foreach ($place in $places) {
    $path = Join-Path $place 'LimitSwitcher.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonw
    $link.Arguments = "-m account_switcher.tray --url-file `"$root\.runtime\tray.url`""
    $link.WorkingDirectory = $root
    $link.IconLocation = "$root\account_switcher\static\assets\switcher.ico,0"
    $link.Description = 'Claude Code and Codex usage limits and account switching'
    $link.Save()
    Write-Host "Created $path"
}
