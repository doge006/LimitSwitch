# Adds "Account Switcher" to the Start menu (and, with -Desktop, the desktop), with the app icon.
# Run again at any time; it overwrites the shortcut. -Remove deletes it.
param([switch]$Desktop, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$places = @([Environment]::GetFolderPath('Programs'))
if ($Desktop) { $places += [Environment]::GetFolderPath('Desktop') }

if ($Remove) {
    foreach ($place in $places) { Remove-Item -LiteralPath (Join-Path $place 'Account Switcher.lnk') -ErrorAction SilentlyContinue }
    Write-Host 'Shortcut removed.'
    exit 0
}

# The same windowless Python Launch.cmd uses, so no console window flashes up.
$pythonw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    $found = Get-Command pythonw.exe, pyw.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $found) { throw 'pythonw.exe not found. Install Python 3.12+ from python.org first.' }
    $pythonw = $found.Source
}

$shell = New-Object -ComObject WScript.Shell
foreach ($place in $places) {
    $path = Join-Path $place 'Account Switcher.lnk'
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonw
    $link.Arguments = "-m account_switcher.tray --url-file `"$root\.runtime\tray.url`""
    $link.WorkingDirectory = $root
    $link.IconLocation = "$root\account_switcher\static\assets\switcher.ico,0"
    $link.Description = 'Claude Code and Codex usage limits and account switching'
    $link.Save()
    Write-Host "Created $path"
}
