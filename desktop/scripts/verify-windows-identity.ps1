param(
  [string]$AppRoot = "",
  [string]$ExpectedAppId = "com.metis.app"
)

$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
if (-not $AppRoot) {
  $AppRoot = Join-Path $desktopRoot "release\win-unpacked"
}
$appRootResolved = [System.IO.Path]::GetFullPath($AppRoot)
$exePath = Join-Path $appRootResolved "Metis.exe"
$iconPath = Join-Path $appRootResolved "resources\icons\logo.ico"
$builderConfigPath = Join-Path $desktopRoot "electron-builder.yml"
$electronPath = Join-Path $desktopRoot "node_modules\electron\dist\electron.exe"
$shortcutProbe = Join-Path $PSScriptRoot "windows-shortcut-probe.cjs"

foreach ($required in @($exePath, $iconPath, $builderConfigPath, $electronPath, $shortcutProbe)) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
    throw "Windows identity verification input is missing: $required"
  }
}

$builderConfig = Get-Content -LiteralPath $builderConfigPath -Raw
if ($builderConfig -notmatch "(?m)^appId:\s*$([regex]::Escape($ExpectedAppId))\s*$") {
  throw "electron-builder appId does not match $ExpectedAppId"
}
if ($builderConfig -notmatch "(?m)^\s*shortcutName:\s*Metis\s*$") {
  throw "electron-builder shortcutName is not Metis"
}

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class MetisWindowIconNative {
  [DllImport("user32.dll", CharSet = CharSet.Auto)]
  public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll", EntryPoint = "GetClassLongPtr", SetLastError = true)]
  public static extern IntPtr GetClassLongPtr64(IntPtr hWnd, int index);
  [DllImport("user32.dll", EntryPoint = "GetClassLong", SetLastError = true)]
  public static extern IntPtr GetClassLong32(IntPtr hWnd, int index);
  [DllImport("user32.dll", SetLastError = true)]
  public static extern bool DestroyIcon(IntPtr hIcon);
  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  private static extern uint ExtractIconEx(string fileName, int iconIndex, out IntPtr largeIcon, out IntPtr smallIcon, uint iconCount);
  public static IntPtr GetClassIcon(IntPtr hWnd, int index) {
    return IntPtr.Size == 8 ? GetClassLongPtr64(hWnd, index) : GetClassLong32(hWnd, index);
  }
  public static IntPtr ExtractLargeIcon(string fileName) {
    IntPtr largeIcon;
    IntPtr smallIcon;
    uint count = ExtractIconEx(fileName, 0, out largeIcon, out smallIcon, 1);
    if (smallIcon != IntPtr.Zero) DestroyIcon(smallIcon);
    return count == 0 ? IntPtr.Zero : largeIcon;
  }
}
"@

function Test-IconMatchesMetisLogo([System.Drawing.Icon]$ActualIcon) {
  $expectedHandle = [MetisWindowIconNative]::ExtractLargeIcon($iconPath)
  if ($expectedHandle -eq [IntPtr]::Zero) {
    throw "Cannot extract the expected Metis icon from $iconPath"
  }
  $expectedIcon = [System.Drawing.Icon]::FromHandle($expectedHandle)
  $actualSource = $ActualIcon.ToBitmap()
  $expectedSource = $expectedIcon.ToBitmap()
  $actualBitmap = [System.Drawing.Bitmap]::new(32, 32)
  $expectedBitmap = [System.Drawing.Bitmap]::new(32, 32)
  $actualGraphics = [System.Drawing.Graphics]::FromImage($actualBitmap)
  $expectedGraphics = [System.Drawing.Graphics]::FromImage($expectedBitmap)
  try {
    foreach ($graphics in @($actualGraphics, $expectedGraphics)) {
      $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
      $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
      $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    }
    $actualGraphics.DrawImage($actualSource, 0, 0, 32, 32)
    $expectedGraphics.DrawImage($expectedSource, 0, 0, 32, 32)

    $difference = 0L
    for ($y = 0; $y -lt 32; $y++) {
      for ($x = 0; $x -lt 32; $x++) {
        $actual = $actualBitmap.GetPixel($x, $y)
        $expected = $expectedBitmap.GetPixel($x, $y)
        $difference += [Math]::Abs([int]$actual.A - [int]$expected.A)
        $difference += [Math]::Abs([int]$actual.R - [int]$expected.R)
        $difference += [Math]::Abs([int]$actual.G - [int]$expected.G)
        $difference += [Math]::Abs([int]$actual.B - [int]$expected.B)
      }
    }
    $meanChannelDifference = $difference / (4.0 * 32 * 32)
    Write-Host "[verify-windows-identity] normalized icon difference: $([Math]::Round($meanChannelDifference, 3))"
    return $meanChannelDifference -le 12.0
  } finally {
    $expectedGraphics.Dispose()
    $actualGraphics.Dispose()
    $expectedBitmap.Dispose()
    $actualBitmap.Dispose()
    $expectedSource.Dispose()
    $actualSource.Dispose()
    $expectedIcon.Dispose()
    [MetisWindowIconNative]::DestroyIcon($expectedHandle) | Out-Null
  }
}

$peIconHandle = [MetisWindowIconNative]::ExtractLargeIcon($exePath)
if ($peIconHandle -eq [IntPtr]::Zero) {
  throw "Metis.exe has no extractable PE icon"
}
$peIcon = [System.Drawing.Icon]::FromHandle($peIconHandle)
try {
  if (-not (Test-IconMatchesMetisLogo $peIcon)) {
    throw "Metis.exe PE icon does not match resources\icons\logo.ico"
  }
  Write-Host "[verify-windows-identity] PE icon matches Metis logo ($($peIcon.Width)x$($peIcon.Height))"
} finally {
  $peIcon.Dispose()
  [MetisWindowIconNative]::DestroyIcon($peIconHandle) | Out-Null
}

$electronIconHandle = [MetisWindowIconNative]::ExtractLargeIcon($electronPath)
if ($electronIconHandle -eq [IntPtr]::Zero) {
  throw "Cannot extract the default Electron icon used by the negative identity gate"
}
$electronIcon = [System.Drawing.Icon]::FromHandle($electronIconHandle)
try {
  if (Test-IconMatchesMetisLogo $electronIcon) {
    throw "Windows icon gate cannot distinguish the Metis logo from Electron's default icon"
  }
  Write-Host "[verify-windows-identity] default Electron icon is rejected"
} finally {
  $electronIcon.Dispose()
  [MetisWindowIconNative]::DestroyIcon($electronIconHandle) | Out-Null
}

$probeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("metis-windows-identity-" + [guid]::NewGuid().ToString("N"))
$probeUserData = Join-Path $probeRoot "user-data"
$probeHome = Join-Path $probeRoot "metis-home"
$probeShortcut = Join-Path $probeRoot "Metis.identity-check.lnk"
$shortcutResultPath = Join-Path $probeRoot "shortcut-result.json"
New-Item -ItemType Directory -Path $probeUserData, $probeHome -Force | Out-Null

$previousMetisHome = $env:METIS_HOME
$previousMetisDataRoot = $env:METIS_DATA_ROOT
$previousIdentityVerify = $env:METIS_WINDOWS_IDENTITY_VERIFY
$process = $null
try {
  $env:METIS_HOME = $probeHome
  $env:METIS_DATA_ROOT = $probeRoot
  $env:METIS_WINDOWS_IDENTITY_VERIFY = "1"
  $process = Start-Process -FilePath $exePath -ArgumentList @(
    "--metis-windows-identity-verify",
    "--user-data-dir=$probeUserData",
    "--metis-graphics-mode=software"
  ) -PassThru

  $deadline = [DateTime]::UtcNow.AddSeconds(35)
  $windowHandle = [IntPtr]::Zero
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
    if ($process.HasExited) {
      throw "Packaged Metis exited before publishing its main window icon (exit $($process.ExitCode))"
    }
    if ($process.MainWindowHandle -ne 0) {
      $windowHandle = [IntPtr]$process.MainWindowHandle
      break
    }
  }
  if ($windowHandle -eq [IntPtr]::Zero) {
    throw "Packaged Metis did not publish a main window within 35 seconds"
  }

  $WM_GETICON = 0x007F
  $ICON_BIG = [IntPtr]1
  $GCLP_HICON = -14
  $iconHandle = [MetisWindowIconNative]::SendMessage($windowHandle, $WM_GETICON, $ICON_BIG, [IntPtr]::Zero)
  if ($iconHandle -eq [IntPtr]::Zero) {
    $iconHandle = [MetisWindowIconNative]::GetClassIcon($windowHandle, $GCLP_HICON)
  }
  if ($iconHandle -eq [IntPtr]::Zero) {
    throw "Packaged Metis main window did not publish WM_GETICON or a window-class icon"
  }

  $windowIcon = [System.Drawing.Icon]::FromHandle($iconHandle)
  if (-not (Test-IconMatchesMetisLogo $windowIcon)) {
    throw "Packaged Metis main window icon ($($windowIcon.Width)x$($windowIcon.Height)) does not match resources\icons\logo.ico"
  }
  Write-Host "[verify-windows-identity] live window icon matches Metis logo ($($windowIcon.Width)x$($windowIcon.Height))"

  $shortcutProcess = Start-Process -FilePath $electronPath -ArgumentList @(
    $shortcutProbe,
    $shortcutResultPath,
    $probeShortcut,
    $exePath,
    $iconPath,
    $ExpectedAppId
  ) -PassThru
  if (-not $shortcutProcess.WaitForExit(15000)) {
    Stop-Process -Id $shortcutProcess.Id -Force -ErrorAction SilentlyContinue
    throw "Shortcut AppUserModelID probe timed out"
  }
  if (-not (Test-Path -LiteralPath $shortcutResultPath -PathType Leaf)) {
    throw "Shortcut AppUserModelID probe returned no shortcut details"
  }
  $shortcutResult = Get-Content -LiteralPath $shortcutResultPath -Raw | ConvertFrom-Json
  if (-not $shortcutResult.ok) {
    throw "Shortcut AppUserModelID probe failed: $($shortcutResult.error)"
  }
  $shortcut = $shortcutResult.details
  if ($shortcut.appUserModelId -ne $ExpectedAppId) {
    throw "Shortcut AppUserModelID is '$($shortcut.appUserModelId)', expected '$ExpectedAppId'"
  }
  if ([System.IO.Path]::GetFullPath($shortcut.target) -ne $exePath) {
    throw "Shortcut target is '$($shortcut.target)', expected '$exePath'"
  }
  Write-Host "[verify-windows-identity] shortcut AppUserModelID is $ExpectedAppId"
} finally {
  if ($process -and -not $process.HasExited) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    $process.WaitForExit(5000) | Out-Null
  }
  Get-CimInstance Win32_Process -Filter "Name='Metis.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.ExecutablePath -eq $exePath -and
      $_.CommandLine -like "*$probeRoot*"
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  $env:METIS_HOME = $previousMetisHome
  $env:METIS_DATA_ROOT = $previousMetisDataRoot
  $env:METIS_WINDOWS_IDENTITY_VERIFY = $previousIdentityVerify
  Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "[verify-windows-identity] all Windows identity gates passed"
