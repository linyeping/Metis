param(
  [string]$Exe = "",
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$desktopRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$repoRoot = Resolve-Path -LiteralPath (Join-Path $desktopRoot "..")
if ([string]::IsNullOrWhiteSpace($Exe)) {
  $Exe = Join-Path $desktopRoot "release\metis.exe"
}
$exePath = [System.IO.Path]::GetFullPath($Exe)
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
  throw "Packaged CLI is missing: $exePath"
}
$pythonCommand = Get-Command $Python -ErrorAction Stop

$probeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("metis-cli-attach-probe-" + [guid]::NewGuid().ToString("N"))
$probeHome = Join-Path $probeRoot "home"
$probeLocalAppData = Join-Path $probeRoot "local-app-data"
$discoveryPath = Join-Path $probeLocalAppData "Metis\runtime\desktop-attach.json"
$dataHomePointerPath = Join-Path $probeLocalAppData "Metis\runtime\data-home.json"
New-Item -ItemType Directory -Force -Path $probeRoot, $probeHome, $probeLocalAppData | Out-Null

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$previousMetisHome = $env:METIS_HOME
$previousDiscovery = $env:METIS_CLI_ATTACH_DISCOVERY
$previousAttachChannel = $env:METIS_CLI_ATTACH_CHANNEL
$previousLocalAppData = $env:LOCALAPPDATA
$previousBackend = $env:METIS_LLM_BACKEND
$previousModel = $env:METIS_LLM_MODEL
$backendProcess = $null
try {
  $env:METIS_HOME = $probeHome
  $env:METIS_CLI_ATTACH_DISCOVERY = ""
  $env:METIS_CLI_ATTACH_CHANNEL = "stable"
  $env:LOCALAPPDATA = $probeLocalAppData
  $env:METIS_LLM_BACKEND = "fake"
  $env:METIS_LLM_MODEL = "fake-model"
  $backendProcess = Start-Process `
    -FilePath $pythonCommand.Source `
    -ArgumentList @("-m", "backend", "--mode", "web", "--port", [string]$port) `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $probeRoot "backend.stdout") `
    -RedirectStandardError (Join-Path $probeRoot "backend.stderr") `
    -WindowStyle Hidden `
    -PassThru

  # The desktop backend keeps the custom METIS_HOME inherited above. The
  # standalone CLI deliberately does not inherit it and must discover the
  # desktop data directory through the stable current-user pointer.
  $env:METIS_HOME = ""

  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  while (
    -not (Test-Path -LiteralPath $discoveryPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $dataHomePointerPath -PathType Leaf)
  ) {
    if ($backendProcess.HasExited) {
      $backendError = Get-Content -LiteralPath (Join-Path $probeRoot "backend.stderr") -Raw -ErrorAction SilentlyContinue
      throw "Attach probe backend exited early: $backendError"
    }
    if ([DateTime]::UtcNow -gt $deadline) {
      throw "Attach probe discovery timed out"
    }
    Start-Sleep -Milliseconds 200
  }

  $discovery = Get-Content -LiteralPath $discoveryPath -Raw | ConvertFrom-Json
  if ($discovery.schema -ne "metis.cli_attach.discovery.v1" -or $discovery.protocol -ne "metis.cli_attach.v1") {
    throw "Attach discovery contract is invalid"
  }
  if ($discovery.pid -ne $backendProcess.Id -or $discovery.port -ne $port -or [string]::IsNullOrWhiteSpace($discovery.token)) {
    throw "Attach discovery endpoint does not match the backend process"
  }
  $dataHomePointer = Get-Content -LiteralPath $dataHomePointerPath -Raw | ConvertFrom-Json
  if ($dataHomePointer.schema -ne "metis.cli_data_home.v1" -or $dataHomePointer.metis_home -ne $probeHome) {
    throw "CLI data-home pointer does not match the desktop data directory"
  }

  & $exePath --attach "first packaged attach" --workspace $probeRoot --output-format json 1> (Join-Path $probeRoot "first.json") 2> (Join-Path $probeRoot "first.stderr")
  if ($LASTEXITCODE -ne 0) { throw "Packaged CLI attach probe failed" }
  $first = Get-Content -LiteralPath (Join-Path $probeRoot "first.json") -Raw | ConvertFrom-Json
  if ($first.schema -ne "metis.cli_result.v1" -or $first.exit -ne 0 -or $first.text -notmatch "fake backend response") {
    throw "Packaged CLI attach result contract is invalid"
  }

  & $exePath --attach --resume $first.session_id "resume packaged attach" --workspace $probeRoot --output-format json 1> (Join-Path $probeRoot "resume.json") 2> (Join-Path $probeRoot "resume.stderr")
  if ($LASTEXITCODE -ne 0) { throw "Packaged CLI attach resume probe failed" }
  $resumed = Get-Content -LiteralPath (Join-Path $probeRoot "resume.json") -Raw | ConvertFrom-Json
  if ($resumed.exit -ne 0 -or $resumed.session_id -ne $first.session_id) {
    throw "Packaged CLI attach resume did not preserve the session"
  }

  & $exePath sessions list --output-format json 1> (Join-Path $probeRoot "sessions.json") 2> (Join-Path $probeRoot "sessions.stderr")
  if ($LASTEXITCODE -ne 0) { throw "Packaged CLI shared session-store probe failed" }
  $sessions = Get-Content -LiteralPath (Join-Path $probeRoot "sessions.json") -Raw | ConvertFrom-Json
  if (@($sessions.sessions | Where-Object { $_.id -eq $first.session_id }).Count -ne 1) {
    throw "Packaged CLI did not resolve the desktop data-home pointer"
  }

  $cliOutput = @(
    Get-Content -LiteralPath (Join-Path $probeRoot "first.json") -Raw
    Get-Content -LiteralPath (Join-Path $probeRoot "first.stderr") -Raw
    Get-Content -LiteralPath (Join-Path $probeRoot "resume.json") -Raw
    Get-Content -LiteralPath (Join-Path $probeRoot "resume.stderr") -Raw
  ) -join "`n"
  if ($cliOutput.Contains([string]$discovery.token)) {
    throw "Packaged CLI attach leaked the discovery token"
  }

  Stop-Process -Id $backendProcess.Id -Force
  $backendProcess.WaitForExit()
  $backendProcess = $null
  & $exePath --attach "stale endpoint" --workspace $probeRoot --output-format json 1> (Join-Path $probeRoot "stale.stdout") 2> (Join-Path $probeRoot "stale.stderr")
  if ($LASTEXITCODE -ne 3) { throw "Packaged CLI accepted a stale desktop discovery record" }
  $staleError = Get-Content -LiteralPath (Join-Path $probeRoot "stale.stderr") -Raw
  if ($staleError.Contains([string]$discovery.token)) {
    throw "Packaged CLI stale-endpoint error leaked the discovery token"
  }

  Write-Host "Packaged CLI attach verification passed: $exePath"
}
finally {
  if ($null -ne $backendProcess -and -not $backendProcess.HasExited) {
    Stop-Process -Id $backendProcess.Id -Force
    $backendProcess.WaitForExit()
  }
  $env:METIS_HOME = $previousMetisHome
  $env:METIS_CLI_ATTACH_DISCOVERY = $previousDiscovery
  $env:METIS_CLI_ATTACH_CHANNEL = $previousAttachChannel
  $env:LOCALAPPDATA = $previousLocalAppData
  $env:METIS_LLM_BACKEND = $previousBackend
  $env:METIS_LLM_MODEL = $previousModel

  $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
  $resolvedProbe = [System.IO.Path]::GetFullPath($probeRoot)
  if (
    $resolvedProbe.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase) -and
    (Split-Path -Leaf $resolvedProbe).StartsWith("metis-cli-attach-probe-", [System.StringComparison]::OrdinalIgnoreCase)
  ) {
    Remove-Item -LiteralPath $resolvedProbe -Recurse -Force
  }
}

exit 0
