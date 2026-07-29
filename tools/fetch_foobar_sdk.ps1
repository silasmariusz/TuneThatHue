<#
  Fetch the foobar2000 SDK into foobar/sdk/ so the component can be built.

  The SDK is NOT committed to this repo (its licence lets us ship components
  built with it, but re-distributing the SDK itself is not our call). Run this
  once on a fresh checkout; foobar/sdk/ is git-ignored.

  Needs 7-Zip (the SDK ships as .7z). Install with:  winget install 7zip.7zip
#>
[CmdletBinding()]
param(
    # Bump when foobar2000.org publishes a newer SDK.
    [string]$Version = "2025-03-07",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sdk = Join-Path $root "foobar\sdk"
$archive = Join-Path $sdk "SDK.7z"
$url = "https://www.foobar2000.org/downloads/SDK-$Version.7z"

if ((Test-Path (Join-Path $sdk "pfc")) -and -not $Force) {
    Write-Host "SDK already present at $sdk (use -Force to re-download)"
    exit 0
}

New-Item -ItemType Directory -Force -Path $sdk | Out-Null
Write-Host "downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing

$sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue).Source
if (-not $sevenZip) {
    foreach ($c in @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe")) {
        if (Test-Path $c) { $sevenZip = $c; break }
    }
}
if (-not $sevenZip) {
    throw "7-Zip not found - install it (winget install 7zip.7zip) and re-run; archive is at $archive"
}

Write-Host "extracting ..."
& $sevenZip x $archive "-o$sdk" -y | Out-Null
Remove-Item $archive -ErrorAction SilentlyContinue

if (-not (Test-Path (Join-Path $sdk "pfc"))) { throw "extraction failed - $sdk has no pfc/" }
Write-Host "OK  foobar2000 SDK $Version ready in $sdk"
