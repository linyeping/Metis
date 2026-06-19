# Build the Metis privileged VM service (Go) into desktop/resources/runtime-svc.
# Phase 7.7 — runs as part of the desktop packaging pipeline.
param([string]$Go = "")

$ErrorActionPreference = "Stop"

$svcDir = Resolve-Path (Join-Path $PSScriptRoot "..\..\backend\runtime\metis-vm-svc")
$outDir = Join-Path $PSScriptRoot "..\resources\runtime-svc"
$outExe = Join-Path $outDir "metis-vm-svc.exe"

# Resolve the Go toolchain: explicit param -> PATH -> known portable SDK.
if (-not $Go) { $Go = (Get-Command go -ErrorAction SilentlyContinue).Source }
if (-not $Go) {
  foreach ($c in @("$env:LOCALAPPDATA\..\go-sdk\go\bin\go.exe", "C:\Users\$env:USERNAME\go-sdk\go\bin\go.exe", "C:\Go\bin\go.exe")) {
    if (Test-Path $c) { $Go = $c; break }
  }
}
if (-not $Go) { throw "Go toolchain not found. Install Go or pass -Go <path-to-go.exe>." }

# China-friendly module fetch (direct, no system proxy).
$env:GOPROXY = "https://goproxy.cn,direct"
$env:GOSUMDB = "off"
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Push-Location $svcDir
try {
  Write-Output "[build-svc] go=$Go  dir=$svcDir"
  & $Go build -ldflags "-s -w" -o $outExe .
  if ($LASTEXITCODE -ne 0) { throw "go build failed ($LASTEXITCODE)" }
} finally {
  Pop-Location
}

$mb = [math]::Round((Get-Item $outExe).Length / 1MB, 1)
Write-Output "[build-svc] built $outExe ($mb MB)"
