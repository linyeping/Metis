param(
  [string]$Exe = ""
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
if ([string]::IsNullOrWhiteSpace($Exe)) {
  $Exe = Join-Path $desktopRoot "release\metis.exe"
}
$exePath = [System.IO.Path]::GetFullPath($Exe)
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
  throw "Packaged CLI is missing: $exePath"
}

$probeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("metis-cli-clean-probe-" + [guid]::NewGuid().ToString("N"))
$probeExe = Join-Path $probeRoot "metis.exe"
$probeHome = Join-Path $probeRoot "home"
New-Item -ItemType Directory -Force -Path $probeRoot, $probeHome | Out-Null
Copy-Item -LiteralPath $exePath -Destination $probeExe

$previousPath = $env:PATH
$previousPythonPath = $env:PYTHONPATH
$previousMetisHome = $env:METIS_HOME
$previousPythonHome = $env:PYTHONHOME
try {
  # The runner may have Python and Node installed, but the packaged probe must
  # run with neither runtime discoverable on PATH and outside the source tree.
  $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
  $env:PYTHONPATH = ""
  $env:PYTHONHOME = ""
  $env:METIS_HOME = $probeHome
  if (Get-Command python -ErrorAction SilentlyContinue) {
    throw "Clean CLI probe unexpectedly found Python on PATH"
  }
  if (Get-Command node -ErrorAction SilentlyContinue) {
    throw "Clean CLI probe unexpectedly found Node.js on PATH"
  }

  Push-Location $probeRoot
  try {
    "reply ok" | & $probeExe -p --backend fake --model fake-model --no-desktop --no-mcp --allowed-tools read_file --permission-mode plan --max-turns 2 --output-format text 1> text.stdout 2> text.stderr
    if ($LASTEXITCODE -ne 0) { throw "Packaged CLI stdin/text probe failed" }
    $textOutput = Get-Content -LiteralPath "text.stdout" -Raw
    if ($textOutput -notmatch "fake backend response") { throw "Packaged CLI text output is invalid" }

    & $probeExe "reply ok" -p --backend fake --model fake-model --no-desktop --no-mcp --allowed-tools read_file --permission-mode plan --max-turns 2 --output-format json 1> json.stdout 2> json.stderr
    if ($LASTEXITCODE -ne 0) { throw "Packaged CLI json probe failed" }
    $jsonResult = Get-Content -LiteralPath "json.stdout" -Raw | ConvertFrom-Json
    if ($jsonResult.schema -ne "metis.cli_result.v1" -or $jsonResult.exit -ne 0) {
      throw "Packaged CLI json result contract is invalid"
    }

    & $probeExe "reply ok" -p --backend fake --model fake-model --no-desktop --no-mcp --allowed-tools read_file --permission-mode plan --max-turns 2 --output-format stream-json 1> stream.stdout 2> stream.stderr
    if ($LASTEXITCODE -ne 0) { throw "Packaged CLI stream-json probe failed" }
    $events = @(Get-Content -LiteralPath "stream.stdout" -Encoding utf8 | ForEach-Object { $_ | ConvertFrom-Json })
    if ($events.Count -lt 2 -or @($events | Where-Object { $_.schema -ne "metis.agent_event.v1" }).Count -gt 0) {
      throw "Packaged CLI stream-json contract is invalid"
    }
    if (@($events | Where-Object { $_.kind -eq "done" }).Count -ne 1) {
      throw "Packaged CLI stream-json did not emit exactly one done event"
    }

    & $probeExe sessions list --output-format json 1> sessions.stdout 2> sessions.stderr
    if ($LASTEXITCODE -ne 0) { throw "Packaged CLI sessions list probe failed" }
    $sessions = Get-Content -LiteralPath "sessions.stdout" -Raw | ConvertFrom-Json
    if ($sessions.schema -ne "metis.cli_sessions.v1" -or @($sessions.sessions).Count -lt 3) {
      throw "Packaged CLI sessions list contract is invalid"
    }
    if (@($sessions.sessions | Where-Object { $_.id -eq $jsonResult.session_id }).Count -ne 1) {
      throw "Packaged CLI did not persist the JSON probe session"
    }

    & $probeExe --resume $jsonResult.session_id "follow up" -p --backend fake --model fake-model --no-desktop --no-mcp --permission-mode plan --max-turns 2 --output-format json 1> resume.stdout 2> resume.stderr
    if ($LASTEXITCODE -ne 0) { throw "Packaged CLI resume probe failed" }
    $resumeResult = Get-Content -LiteralPath "resume.stdout" -Raw | ConvertFrom-Json
    if ($resumeResult.schema -ne "metis.cli_result.v1" -or $resumeResult.session_id -ne $jsonResult.session_id) {
      throw "Packaged CLI resume contract is invalid"
    }

    & $probeExe sessions export $jsonResult.session_id --output session-export.json 1> export.stdout 2> export.stderr
    if ($LASTEXITCODE -ne 0) { throw "Packaged CLI session export probe failed" }
    $sessionExport = Get-Content -LiteralPath "session-export.json" -Raw | ConvertFrom-Json
    if ($sessionExport.schema -ne "metis.session_export.v1" -or $sessionExport.session.id -ne $jsonResult.session_id) {
      throw "Packaged CLI session export contract is invalid"
    }

    & $probeExe "task" -p --workspace (Join-Path $probeRoot "missing") 1> usage.stdout 2> usage.stderr
    if ($LASTEXITCODE -ne 64) { throw "Packaged CLI usage error did not exit 64" }
  }
  finally {
    Pop-Location
  }
}
finally {
  $env:PATH = $previousPath
  $env:PYTHONPATH = $previousPythonPath
  $env:PYTHONHOME = $previousPythonHome
  $env:METIS_HOME = $previousMetisHome
}

Write-Host "Packaged CLI clean-path verification passed: $probeExe"

$resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$resolvedProbe = [System.IO.Path]::GetFullPath($probeRoot)
if (
  $resolvedProbe.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase) -and
  (Split-Path -Leaf $resolvedProbe).StartsWith("metis-cli-clean-probe-", [System.StringComparison]::OrdinalIgnoreCase)
) {
  Remove-Item -LiteralPath $resolvedProbe -Recurse -Force
}

# The negative usage probe intentionally leaves the native process exit code
# at 64.  A PowerShell script otherwise propagates that stale code even though
# every assertion above passed.
exit 0
