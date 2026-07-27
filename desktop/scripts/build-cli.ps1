param(
  [string]$Python = "",
  [switch]$RecreateVenv,
  [switch]$SkipDependencyInstall,
  [switch]$SkipCliSelfTest,
  [double]$MaxExeMB = 250
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$repoRoot = Resolve-Path -LiteralPath (Join-Path $desktopRoot "..")
$backendRoot = Join-Path $repoRoot "backend"
$releaseRoot = Join-Path $desktopRoot "release"
$workRoot = Join-Path $desktopRoot "resources\cli-build"
$venvRoot = Join-Path $desktopRoot "resources\backend-build\venv"
$pyinstallerWorkRoot = Join-Path $workRoot "pyinstaller"
$specPath = Join-Path $PSScriptRoot "build-cli.spec"
$requirementsPath = Join-Path $backendRoot "requirements-build.txt"
$exe = Join-Path $releaseRoot "metis.exe"
$iconPath = Join-Path $desktopRoot "resources\icons\logo.ico"

if ([string]::IsNullOrWhiteSpace($Python)) {
  $Python = if ([string]::IsNullOrWhiteSpace($env:METIS_PYTHON)) { "python" } else { $env:METIS_PYTHON }
}

$resolvedDesktop = [System.IO.Path]::GetFullPath($desktopRoot)
$resolvedWork = [System.IO.Path]::GetFullPath($workRoot)
if (-not $resolvedWork.StartsWith($resolvedDesktop, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "CLI build work path escaped desktop root: $resolvedWork"
}

if ($RecreateVenv -and (Test-Path -LiteralPath $venvRoot)) {
  $resolvedVenv = [System.IO.Path]::GetFullPath($venvRoot)
  if (-not $resolvedVenv.StartsWith($resolvedDesktop, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "CLI build venv path escaped desktop root: $resolvedVenv"
  }
  Remove-Item -LiteralPath $venvRoot -Recurse -Force
}

if (-not (Test-Path -LiteralPath (Join-Path $venvRoot "Scripts\python.exe"))) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $venvRoot) | Out-Null
  & $Python -m venv $venvRoot
  if ($LASTEXITCODE -ne 0) { throw "Failed to create CLI build venv" }
}

$buildPython = Join-Path $venvRoot "Scripts\python.exe"
if (-not $SkipDependencyInstall) {
  & $buildPython -m pip install --upgrade pip setuptools wheel
  if ($LASTEXITCODE -ne 0) { throw "Failed to update CLI build environment" }
  & $buildPython -m pip install -r $requirementsPath
  if ($LASTEXITCODE -ne 0) { throw "Failed to install CLI build requirements" }
}

& $buildPython -c "import sys; sys.path.insert(0, sys.argv[1]); import astor; from backend.tools.coding.modify_refactor.modify_ast.edit_code_ast import ASTCodeEditor" "$repoRoot"
if ($LASTEXITCODE -ne 0) {
  throw "CLI build environment is missing the AST edit toolchain"
}

if (Test-Path -LiteralPath $pyinstallerWorkRoot) {
  Remove-Item -LiteralPath $pyinstallerWorkRoot -Recurse -Force
}
if (Test-Path -LiteralPath $exe) {
  Remove-Item -LiteralPath $exe -Force
}
New-Item -ItemType Directory -Force -Path $releaseRoot, $pyinstallerWorkRoot | Out-Null

Push-Location $repoRoot
try {
  & $buildPython -m PyInstaller --noconfirm --clean --distpath $releaseRoot --workpath $pyinstallerWorkRoot $specPath
  if ($LASTEXITCODE -ne 0) { throw "CLI PyInstaller build failed with exit code $LASTEXITCODE" }
}
finally {
  Pop-Location
}

if (-not (Test-Path -LiteralPath $exe)) {
  throw "CLI executable missing: $exe"
}

$exeSizeMB = [Math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 2)
Write-Host ("metis.exe size: {0} MB" -f $exeSizeMB)
if ($MaxExeMB -gt 0 -and $exeSizeMB -gt $MaxExeMB) {
  throw ("metis.exe size {0} MB exceeds budget {1} MB" -f $exeSizeMB, $MaxExeMB)
}

if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
  throw "Metis CLI icon is missing: $iconPath"
}
Add-Type -AssemblyName System.Drawing
$actualIcon = [System.Drawing.Icon]::ExtractAssociatedIcon($exe)
if ($null -eq $actualIcon) {
  throw "metis.exe has no extractable PE icon"
}
$expectedIcon = [System.Drawing.Icon]::new($iconPath, 32, 32)
try {
  $actualBitmap = $actualIcon.ToBitmap()
  $expectedBitmap = $expectedIcon.ToBitmap()
  try {
    $difference = 0L
    for ($y = 0; $y -lt 32; $y++) {
      for ($x = 0; $x -lt 32; $x++) {
        $actualPixel = $actualBitmap.GetPixel($x, $y)
        $expectedPixel = $expectedBitmap.GetPixel($x, $y)
        $difference += [Math]::Abs([int]$actualPixel.A - [int]$expectedPixel.A)
        $difference += [Math]::Abs([int]$actualPixel.R - [int]$expectedPixel.R)
        $difference += [Math]::Abs([int]$actualPixel.G - [int]$expectedPixel.G)
        $difference += [Math]::Abs([int]$actualPixel.B - [int]$expectedPixel.B)
      }
    }
    $meanChannelDifference = $difference / (4.0 * 32 * 32)
    if ($meanChannelDifference -gt 12.0) {
      throw ("metis.exe PE icon differs from Metis logo: {0}" -f [Math]::Round($meanChannelDifference, 3))
    }
    Write-Host ("metis.exe PE icon matches Metis logo: {0}" -f [Math]::Round($meanChannelDifference, 3))
  }
  finally {
    $actualBitmap.Dispose()
    $expectedBitmap.Dispose()
  }
}
finally {
  $actualIcon.Dispose()
  $expectedIcon.Dispose()
}

$archiveViewer = Join-Path $venvRoot "Scripts\pyi-archive_viewer.exe"
if (-not (Test-Path -LiteralPath $archiveViewer -PathType Leaf)) {
  throw "PyInstaller archive viewer is missing: $archiveViewer"
}
$archiveListing = (& $archiveViewer -r -b $exe 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
  throw "Cannot inspect packaged CLI archive"
}
foreach ($requiredModule in @("backend.cli.tui", "prompt_toolkit.application.application", "prompt_toolkit.shortcuts.prompt")) {
  if ($archiveListing -notmatch [regex]::Escape($requiredModule)) {
    throw "Packaged CLI is missing TUI module: $requiredModule"
  }
}
Write-Host "Packaged CLI contains the interactive TUI runtime"

if (-not $SkipCliSelfTest) {
  $versionOutput = (& $exe --version 2>&1 | Out-String)
  $versionExitCode = $LASTEXITCODE
  if ($versionExitCode -ne 0 -or $versionOutput -notmatch "Metis 26\.7\.27") {
    throw "Packaged CLI --version self-test failed"
  }
  $helpOutput = (& $exe --help 2>&1 | Out-String)
  $helpExitCode = $LASTEXITCODE
  if ($helpExitCode -ne 0 -or $helpOutput -notmatch "Metis agent CLI") {
    throw "Packaged CLI --help self-test failed"
  }

  $smokeJsonl = Join-Path $workRoot "smoke.jsonl"
  $smokeStderr = Join-Path $workRoot "smoke.stderr.log"
  & $exe "reply ok" -p --backend fake --model fake-model --no-desktop --no-mcp --allowed-tools read_file --permission-mode plan --max-turns 2 --output-format stream-json 1> $smokeJsonl 2> $smokeStderr
  if ($LASTEXITCODE -ne 0) {
    throw "Packaged CLI runtime self-test failed. See $smokeStderr"
  }
  $events = @(Get-Content -LiteralPath $smokeJsonl -Encoding utf8 | ForEach-Object { $_ | ConvertFrom-Json })
  if ($events.Count -lt 2 -or @($events | Where-Object { $_.schema -ne "metis.agent_event.v1" }).Count -gt 0) {
    throw "Packaged CLI emitted an invalid stream-json contract"
  }
  if (@($events | Where-Object { $_.kind -eq "done" }).Count -ne 1) {
    throw "Packaged CLI runtime self-test did not emit done"
  }
  Write-Host "Packaged CLI runtime self-test passed"
}

Get-Item -LiteralPath $exe
