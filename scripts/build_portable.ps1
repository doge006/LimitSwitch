# Builds the portable Windows copy: build\Account Switcher\ (the exe, the app, its own Python)
# and build\AccountSwitcher-windows.zip. Needs Python 3.13 x64 on PATH (for pip) and, for the
# exe, the MSVC tools (cl, rc) on PATH. Used by .github/workflows/release.yml.
param([string]$Out = "build")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$pyver = (python -c "import platform; print(platform.python_version())").Trim()
if (-not $pyver.StartsWith('3.13')) { throw "Python 3.13 is needed to build (found $pyver)" }
$app = Join-Path $Out 'Account Switcher'
Remove-Item -LiteralPath $Out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $app | Out-Null

# 1. Python: the official embeddable package, with site-packages turned on.
$runtime = Join-Path $app 'runtime'
$embed = Join-Path $Out "python-$pyver-embed-amd64.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip" -OutFile $embed
Expand-Archive -LiteralPath $embed -DestinationPath $runtime
Remove-Item -LiteralPath $embed
$pth = Get-ChildItem -LiteralPath $runtime -Filter 'python3*._pth' | Select-Object -First 1
$zipName = (Get-ChildItem -LiteralPath $runtime -Filter 'python3*.zip' | Select-Object -First 1).Name
Set-Content -LiteralPath $pth.FullName -Value @($zipName, '.', 'Lib\site-packages', 'import site') -Encoding ascii

# 2. The app's packages (Windows only: Pillow, pystray).
python -m pip install --quiet --disable-pip-version-check --no-compile --only-binary=:all: `
    --target (Join-Path $runtime 'Lib\site-packages') -r requirements-native.txt

# 3. The app itself.
Copy-Item -Recurse -LiteralPath account_switcher -Destination $app
Get-ChildItem -LiteralPath (Join-Path $app 'account_switcher') -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Copy-Item -LiteralPath AccountSwitcher.pyw, README.md, THIRD-PARTY-NOTICES.txt -Destination $app

# 4. Account Switcher.exe, with the app icon.
rc /nologo /fo (Join-Path $Out 'launcher.res') scripts\win_launcher.rc
cl /nologo /O2 /W3 /DUNICODE /D_UNICODE scripts\win_launcher.c (Join-Path $Out 'launcher.res') `
    /Fo"$Out\\" /Fe"$app\Account Switcher.exe" /link /SUBSYSTEM:WINDOWS user32.lib
if (-not (Test-Path -LiteralPath (Join-Path $app 'Account Switcher.exe'))) { throw 'the launcher did not build' }
Remove-Item -LiteralPath (Join-Path $Out 'launcher.res'), (Join-Path $Out 'win_launcher.obj') -ErrorAction SilentlyContinue

# 5. The zip: one "Account Switcher" folder inside.
Compress-Archive -LiteralPath $app -DestinationPath (Join-Path $Out 'AccountSwitcher-windows.zip') -CompressionLevel Optimal
$size = [math]::Round((Get-Item (Join-Path $Out 'AccountSwitcher-windows.zip')).Length / 1MB, 1)
Write-Host "Built $Out\AccountSwitcher-windows.zip ($size MB), Python $pyver"
