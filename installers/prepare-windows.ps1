<#
    Assemble what the Windows daemon installer needs, then build it.

    The installer ships a Python of its own rather than asking someone to install one:
    an embeddable CPython from python.org, plus the packages the daemon imports, plus
    the ffmpeg build for decoding. That is the difference between "download and run"
    and "first install Python, then pip install six things".

        .\installers\prepare-windows.ps1            assemble and build
        .\installers\prepare-windows.ps1 -SkipBuild assemble only
#>
param(
    [string]$PythonVersion = "3.13.1",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "runtime\python-win"
$Packages = @("aiohttp", "zeroconf", "aiosendspin", "hue-entertainment", "numpy",
              "pystray", "pillow")

Write-Host "==> python runtime" -ForegroundColor Cyan
if (Test-Path (Join-Path $Runtime "python.exe")) {
    Write-Host "    already assembled"
} else {
    New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
    $zip = Join-Path $env:TEMP "python-embed.zip"
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "    downloading $PythonVersion"
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $Runtime -Force
    Remove-Item $zip

    # The embeddable build ignores site-packages until the ._pth file says otherwise,
    # which is what makes pip-installed packages importable.
    $pth = Get-ChildItem -Path $Runtime -Filter "python*._pth" | Select-Object -First 1
    if ($pth) {
        $text = Get-Content $pth.FullName
        if ($text -notcontains "import site") {
            Add-Content -Path $pth.FullName -Value "import site"
        }
        Add-Content -Path $pth.FullName -Value "Lib\site-packages"
    }

    Write-Host "    installing pip"
    $getpip = Join-Path $env:TEMP "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip
    & (Join-Path $Runtime "python.exe") $getpip --no-warn-script-location
    Remove-Item $getpip
}

Write-Host "==> packages" -ForegroundColor Cyan
& (Join-Path $Runtime "python.exe") -m pip install --quiet --upgrade $Packages
if ($LASTEXITCODE -ne 0) { throw "pip failed" }

Write-Host "==> ffmpeg" -ForegroundColor Cyan
$FfmpegDir = Join-Path $Root "runtime\ffmpeg-AMD64"
if (Test-Path (Join-Path $FfmpegDir "ffmpeg.exe")) {
    Write-Host "    already here"
} else {
    Write-Host "    MISSING - put a Windows ffmpeg.exe in runtime\ffmpeg-AMD64\"
    Write-Host "    Without it only uncompressed audio plays. An LGPL build from"
    Write-Host "    https://www.gyan.dev/ffmpeg/builds/ (the 'shared' LGPL one) is fine,"
    Write-Host "    or build one with the same flags as qnap\build_ffmpeg.sh."
}

if ($SkipBuild) { Write-Host "assembled; skipping the build"; exit 0 }

Write-Host "==> installer" -ForegroundColor Cyan
$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup not found - winget install JRSoftware.InnoSetup" }
& $iscc (Join-Path $PSScriptRoot "tth-daemon.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
Write-Host "done -> installers\Output" -ForegroundColor Green
