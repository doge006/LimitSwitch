$ErrorActionPreference = 'Stop'
$env:GOTOOLCHAIN = 'local'
$env:GOMODCACHE = Join-Path $PSScriptRoot 'gomodcache'
$env:GOCACHE = Join-Path $PSScriptRoot 'gocache'
# The proxy's source is not kept in this repo: fetch CLIProxyAPI at the pinned commit and apply our patch.
$source = Join-Path $PSScriptRoot 'proxy-fork'
if (-not (Test-Path -LiteralPath $source)) {
    git clone --quiet https://github.com/router-for-me/CLIProxyAPI $source
    git -C $source checkout --quiet 9bdde54b59d1af70ae0534a0ef61b2c3361a1257
    git -C $source apply (Join-Path $PSScriptRoot 'account-switcher.patch')
    if ($LASTEXITCODE -ne 0) { throw 'Could not fetch or patch the proxy source' }
}
Push-Location $source
try {
    $goCommand = Get-Command go -ErrorAction SilentlyContinue
    $goExe = if ($goCommand) { $goCommand.Source } else { Join-Path $PSScriptRoot 'toolchain\go\bin\go.exe' }
    if (-not (Test-Path -LiteralPath $goExe)) { throw 'Install Go 1.26 or newer and add it to PATH.' }
    & $goExe build -trimpath '-ldflags=-s -w' -o (Join-Path $PSScriptRoot 'cli-proxy-api.exe') ./cmd/server
    if ($LASTEXITCODE -ne 0) { throw 'Go build failed' }
} finally { Pop-Location }
