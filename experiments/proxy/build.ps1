$ErrorActionPreference = 'Stop'
$env:GOTOOLCHAIN = 'local'
$env:GOMODCACHE = Join-Path $PSScriptRoot 'gomodcache'
$env:GOCACHE = Join-Path $PSScriptRoot 'gocache'
Push-Location (Join-Path $PSScriptRoot '..\..\proxy-fork')
try {
    $goCommand = Get-Command go -ErrorAction SilentlyContinue
    $goExe = if ($goCommand) { $goCommand.Source } else { Join-Path $PSScriptRoot 'toolchain\go\bin\go.exe' }
    if (-not (Test-Path -LiteralPath $goExe)) { throw 'Install Go 1.26 or newer and add it to PATH.' }
    & $goExe build -trimpath '-ldflags=-s -w' -o (Join-Path $PSScriptRoot 'cli-proxy-api.exe') ./cmd/server
    if ($LASTEXITCODE -ne 0) { throw 'Go build failed' }
} finally { Pop-Location }
