<#
  Build every TuneThatHue Windows artifact: the SoundRecorder capture app, the
  foobar2000 component (+ .fb2k-component package) and the Inno Setup installers.

  Requirements
    * Visual Studio Build Tools with the "Desktop development with C++" workload
      (found automatically via vswhere).
    * Python + Pillow, for the icon (tools\make_icon.py).
    * foobar2000 SDK in foobar\sdk - run tools\fetch_foobar_sdk.ps1 once.
    * Inno Setup 6 (ISCC.exe) for the installers - winget install JRSoftware.InnoSetup.
      Missing tools are reported and only their step is skipped; the rest still builds.

  Usage
    .\build-windows.ps1                # everything that is available
    .\build-windows.ps1 -SkipInstallers
    .\build-windows.ps1 -RebuildWinamp # also rebuild the 32-bit Winamp plugin

  The Winamp plugin is NOT rebuilt by default: the shipped DLL is a tested build
  and rebuilding it only risks a regression for no gain.

  Code signing: unsigned binaries make SmartScreen complain and some antivirus
  products quarantine them. Set $env:TTH_SIGN_CERT (a .pfx path) plus
  $env:TTH_SIGN_PASS to sign everything as it is produced.
#>
[CmdletBinding()]
param(
    [switch]$SkipInstallers,
    [switch]$RebuildWinamp,
    [switch]$SkipFoobar
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$built = [System.Collections.Generic.List[string]]::new()
$skipped = [System.Collections.Generic.List[string]]::new()

function Find-VcVars {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $p = & $vswhere -latest -products * `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath 2>$null
        if ($p) { return Join-Path $p "VC\Auxiliary\Build\vcvarsall.bat" }
    }
    foreach ($c in @(
            "D:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
            "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat")) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Find-ISCC {
    foreach ($c in @(
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $c) { return $c }
    }
    return (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
}

# Sign one file when a certificate is configured; a no-op otherwise.
function Invoke-Sign([string]$file) {
    if (-not $env:TTH_SIGN_CERT) { return }
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe `
        -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match "x64" } | Select-Object -Last 1
    if (-not $signtool) { Write-Warning "signtool.exe not found - $file left unsigned"; return }
    & $signtool.FullName sign /fd SHA256 /f $env:TTH_SIGN_CERT /p $env:TTH_SIGN_PASS `
        /tr http://timestamp.digicert.com /td SHA256 $file | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "    signed $([IO.Path]::GetFileName($file))" }
}

$vcvars = Find-VcVars
if (-not $vcvars) { throw "Visual Studio C++ build tools not found - install the 'Desktop development with C++' workload." }
Write-Host "MSVC: $vcvars"

# --- 1. icon -----------------------------------------------------------------
Write-Host "`n[1/5] icon"
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = "C:\Python314\python.exe" }
if ((Test-Path $python) -and (Test-Path "Z:\t\t.png")) {
    & $python "$root\tools\make_icon.py"
} elseif (Test-Path "$root\resources\tth.ico") {
    Write-Host "    using the committed resources\tth.ico"
} else {
    throw "no icon: resources\tth.ico is missing and the source PNG is unavailable"
}

# --- 2. SoundRecorder --------------------------------------------------------
Write-Host "`n[2/5] SoundRecorder (x64)"
& "$root\soundrecorder\build.bat" | Select-Object -Last 1
$sr = "$root\soundrecorder\TuneThatHue-SoundRecorder.exe"
if (Test-Path $sr) { Invoke-Sign $sr; $built.Add("soundrecorder\TuneThatHue-SoundRecorder.exe") }
else { throw "SoundRecorder build failed" }

# --- 3. foobar2000 component -------------------------------------------------
Write-Host "`n[3/5] foobar2000 component (x64)"
if ($SkipFoobar) {
    $skipped.Add("foobar component (-SkipFoobar)")
} elseif (-not (Test-Path "$root\foobar\sdk\pfc")) {
    $skipped.Add("foobar component (SDK missing - run tools\fetch_foobar_sdk.ps1)")
} else {
    # foobar2000 v2 ships as 32-bit AND 64-bit, and a component that does not
    # match the player fails to load with "Not a valid Win32 application".
    # Build both and ship one package that covers either.
    & "$root\foobar\build.bat" x86 | Select-Object -Last 1
    & "$root\foobar\build.bat" x64 | Select-Object -Last 1
    $dll32 = "$root\foobar\foo_tunethathue-x86.dll"
    $dll64 = "$root\foobar\foo_tunethathue-x64.dll"
    if ((Test-Path $dll32) -and (Test-Path $dll64)) {
        Invoke-Sign $dll32
        Invoke-Sign $dll64
        # A .fb2k-component is a plain zip; double-clicking it installs it.
        # Dual-architecture layout: 32-bit at the root, 64-bit under x64\.
        $pkgDir = "$root\installers\Output"
        New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
        $stage = "$env:TEMP\tth_fb2k_stage"
        Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path "$stage\x64" | Out-Null
        Copy-Item $dll32 "$stage\foo_tunethathue.dll" -Force
        Copy-Item $dll64 "$stage\x64\foo_tunethathue.dll" -Force
        $zip = "$pkgDir\foo_tunethathue.zip"
        $component = "$pkgDir\foo_tunethathue.fb2k-component"
        Remove-Item $zip, $component -ErrorAction SilentlyContinue
        Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force
        Move-Item $zip $component -Force
        Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
        $built.Add("installers\Output\foo_tunethathue.fb2k-component (x86 + x64)")
    } else { throw "foobar component build failed" }
}

# --- 4. Winamp plugin --------------------------------------------------------
Write-Host "`n[4/5] Winamp plugin (x86)"
if ($RebuildWinamp) {
    if (Test-Path "$root\winamp\build.bat") {
        & "$root\winamp\build.bat" | Select-Object -Last 1
    } else { $skipped.Add("Winamp rebuild (no winamp\build.bat)") }
} else {
    Write-Host "    shipping the existing tested DLL (use -RebuildWinamp to rebuild)"
}
$wa = "$root\winamp\dsp_tunethathue.dll"
if (Test-Path $wa) { Invoke-Sign $wa; $built.Add("winamp\dsp_tunethathue.dll") }
else { $skipped.Add("Winamp plugin (dsp_tunethathue.dll missing)") }

# --- 5. installers -----------------------------------------------------------
Write-Host "`n[5/5] installers"
$iscc = Find-ISCC
if ($SkipInstallers) {
    $skipped.Add("installers (-SkipInstallers)")
} elseif (-not $iscc) {
    $skipped.Add("installers (Inno Setup not found - winget install JRSoftware.InnoSetup)")
} else {
    New-Item -ItemType Directory -Force -Path "$root\installers\Output" | Out-Null
    $scripts = @("tth-soundrecorder.iss")
    if (Test-Path $wa) { $scripts += "tth-winamp.iss" }
    if (Test-Path "$root\foobar\foo_tunethathue.dll") { $scripts += "tth-foobar.iss" }
    foreach ($s in $scripts) {
        Write-Host "    ISCC $s"
        & $iscc /Q "$root\installers\$s"
        if ($LASTEXITCODE -ne 0) { throw "ISCC failed on $s" }
    }
    Get-ChildItem "$root\installers\Output\*setup.exe" | ForEach-Object {
        Invoke-Sign $_.FullName
        $built.Add("installers\Output\$($_.Name)")
    }
}

# --- summary -----------------------------------------------------------------
Write-Host "`n================ build summary ================"
foreach ($b in $built) { Write-Host "  built   $b" }
foreach ($s in $skipped) { Write-Host "  skipped $s" }
if (-not $env:TTH_SIGN_CERT) {
    Write-Host "`n  note: binaries are UNSIGNED - Windows SmartScreen will warn users and"
    Write-Host "        some antivirus products may quarantine them. Set TTH_SIGN_CERT/"
    Write-Host "        TTH_SIGN_PASS to sign."
}
Write-Host "===============================================`n"
