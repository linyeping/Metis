param(
  [string]$Python = "",
  [switch]$RecreateVenv,
  [switch]$SkipDependencyInstall,
  [switch]$SkipBackendSelfTest,
  [double]$MaxDistMB = 250
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$repoRoot = Resolve-Path -LiteralPath (Join-Path $desktopRoot "..")
$backendRoot = Join-Path $repoRoot "backend"
$distRoot = Join-Path $desktopRoot "resources\backend-dist"
$workRoot = Join-Path $desktopRoot "resources\backend-build"
$venvRoot = Join-Path $workRoot "venv"
$pyinstallerWorkRoot = Join-Path $workRoot "pyinstaller"
$specPath = Join-Path $PSScriptRoot "build-backend.spec"
$requirementsPath = Join-Path $backendRoot "requirements-build.txt"
$tmpReleaseRoot = Join-Path $desktopRoot "release\win-unpacked.tmp"

if ([string]::IsNullOrWhiteSpace($Python)) {
  if (-not [string]::IsNullOrWhiteSpace($env:METIS_PYTHON)) {
    $Python = $env:METIS_PYTHON
  } else {
    $Python = "python"
  }
}

if (-not (Test-Path -LiteralPath $backendRoot)) {
  throw "backend root not found: $backendRoot"
}

if (-not (Test-Path -LiteralPath $specPath)) {
  throw "PyInstaller spec not found: $specPath"
}

if (-not (Test-Path -LiteralPath $requirementsPath)) {
  throw "Backend build requirements not found: $requirementsPath"
}

if (-not [string]::IsNullOrWhiteSpace($env:METIS_BACKEND_DIST_MAX_MB)) {
  $MaxDistMB = [double]$env:METIS_BACKEND_DIST_MAX_MB
}

if ($RecreateVenv -and (Test-Path -LiteralPath $venvRoot)) {
  Remove-Item -LiteralPath $venvRoot -Recurse -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
  New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
  & $Python -m venv $venvRoot
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to create backend build venv with $Python"
  }
}

$buildPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $buildPython)) {
  throw "Backend build venv python missing: $buildPython"
}

if (-not $SkipDependencyInstall) {
  & $buildPython -m pip install --upgrade pip setuptools wheel
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade backend build venv bootstrap packages"
  }
  & $buildPython -m pip install -r $requirementsPath
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to install backend build requirements from $requirementsPath"
  }
}

& $buildPython -c "import flask, requests, PyInstaller"
if ($LASTEXITCODE -ne 0) {
  throw "Backend build preflight failed inside the clean venv."
}

Remove-Item -LiteralPath $distRoot, $pyinstallerWorkRoot -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $tmpReleaseRoot) {
  Remove-Item -LiteralPath $tmpReleaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $distRoot, $pyinstallerWorkRoot | Out-Null

$args = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--distpath", $distRoot,
  "--workpath", $pyinstallerWorkRoot,
  $specPath
)

Push-Location $repoRoot
try {
  & $buildPython @args
  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
  }
}
finally {
  Pop-Location
}

Get-ChildItem -LiteralPath $distRoot -Recurse -Force -Filter ".env" | ForEach-Object {
  Remove-Item -LiteralPath $_.FullName -Force
}
Get-ChildItem -LiteralPath $distRoot -Recurse -Force -File | Where-Object {
  $_.Extension -in @(".key", ".pfx")
} | ForEach-Object {
  Remove-Item -LiteralPath $_.FullName -Force
}

$exe = Join-Path $distRoot "metis-backend\metis-backend.exe"
if (-not (Test-Path -LiteralPath $exe)) {
  throw "PyInstaller output missing: $exe"
}

function Get-DirectorySizeBytes([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return 0
  }
  $sum = Get-ChildItem -LiteralPath $Path -Recurse -Force -File | Measure-Object -Property Length -Sum
  return [int64]($sum.Sum)
}

$distSizeBytes = Get-DirectorySizeBytes $distRoot
$distSizeMB = [Math]::Round($distSizeBytes / 1MB, 2)
Write-Host ("backend-dist size: {0} MB" -f $distSizeMB)
if ($MaxDistMB -gt 0 -and $distSizeMB -gt $MaxDistMB) {
  throw ("backend-dist size {0} MB exceeds budget {1} MB" -f $distSizeMB, $MaxDistMB)
}

if (-not $SkipBackendSelfTest) {
  $port = Get-Random -Minimum 44000 -Maximum 55999
  $selfTestRoot = Join-Path $workRoot "selftest-data"
  New-Item -ItemType Directory -Force -Path $selfTestRoot | Out-Null
  $stdoutLog = Join-Path $workRoot "backend-selftest.out.log"
  $stderrLog = Join-Path $workRoot "backend-selftest.err.log"
  $oldHttpPort = $env:METIS_HTTP_PORT
  $oldPort = $env:METIS_PORT
  $oldDataRoot = $env:METIS_DATA_ROOT
  $oldDisableDesktop = $env:METIS_DISABLE_DESKTOP_TOOLS
  $oldDisableMcp = $env:METIS_DISABLE_MCP
  $process = $null
  try {
    $env:METIS_HTTP_PORT = [string]$port
    $env:METIS_PORT = [string]$port
    $env:METIS_DATA_ROOT = $selfTestRoot
    $env:METIS_DISABLE_DESKTOP_TOOLS = "1"
    $env:METIS_DISABLE_MCP = "1"
    $process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $deadline = (Get-Date).AddSeconds(25)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
      if ($process.HasExited) {
        break
      }
      try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
          $healthy = $true
          break
        }
      }
      catch {
        Start-Sleep -Milliseconds 500
      }
    }
    if (-not $healthy) {
      throw "Packaged backend self-test failed. See $stdoutLog and $stderrLog"
    }
    Write-Host "Packaged backend self-test passed on port $port"
  }
  finally {
    if ($process -ne $null -and -not $process.HasExited) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    $env:METIS_HTTP_PORT = $oldHttpPort
    $env:METIS_PORT = $oldPort
    $env:METIS_DATA_ROOT = $oldDataRoot
    $env:METIS_DISABLE_DESKTOP_TOOLS = $oldDisableDesktop
    $env:METIS_DISABLE_MCP = $oldDisableMcp
  }
}

Get-Item -LiteralPath $exe
