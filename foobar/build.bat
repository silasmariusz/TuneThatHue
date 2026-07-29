@echo off
rem Build foo_tunethathue.dll (foobar2000 component) with MSVC.
rem
rem   build.bat            -> x64   (foobar2000 v2 64-bit)
rem   build.bat x86        -> Win32 (foobar2000 v2 32-bit, and v1.x)
rem
rem foobar2000 v2 ships in both flavours and the component must match the player
rem or it refuses to load with "Not a valid Win32 application". Output is
rem foo_tunethathue-<arch>.dll so both can sit side by side; build-windows.ps1
rem packs them into ONE .fb2k-component (32-bit at the root, 64-bit under x64\),
rem which is the layout foobar expects for a dual-architecture component.
rem
rem Route "direct cl.exe": compile the SDK sources we actually need (pfc +
rem foobar2000/SDK + the component client) straight into the DLL, then link the
rem prebuilt shared-*.lib that ships with the SDK. This deliberately avoids
rem the SDK's vcxproj files (which target older toolsets) and libPPUI/helpers
rem (which need ATL - not part of the Build Tools C++ workload).
rem
rem Run tools\fetch_foobar_sdk.ps1 first to populate sdk\.
setlocal enabledelayedexpansion

set ARCH=%~1
if "%ARCH%"=="" set ARCH=x64
if /I "%ARCH%"=="x86" (
  set VCARCH=x86
  set MACHINE=X86
  set SHAREDLIB=shared-Win32.lib
) else if /I "%ARCH%"=="x64" (
  set VCARCH=x64
  set MACHINE=X64
  set SHAREDLIB=shared-x64.lib
) else (
  echo BAD_ARCH %ARCH% - use x86 or x64 & exit /b 1
)

set VCVARS=D:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat
call "%VCVARS%" %VCARCH% >nul
if errorlevel 1 ( echo VCVARS_FAILED & exit /b 1 )

cd /d "%~dp0"
if not exist sdk\pfc ( echo SDK_MISSING - run tools\fetch_foobar_sdk.ps1 & exit /b 1 )

rem Per-arch object dir: objects from one architecture must never reach the other
rem linker, and a stale mix is the kind of failure that only shows up at runtime.
set OBJDIR=build_obj\%ARCH%
if exist %OBJDIR% rd /s /q %OBJDIR%
mkdir %OBJDIR%

rem The SDK is C++ with exceptions + RTTI; UNICODE matches how foobar builds it.
rem The SDK requires C++17 (pfc-lite.h enforces it).
rem NDEBUG: this is an optimised build, and without it the SDK warns and keeps
rem its debug-only assertions compiled in.
set CFLAGS=/nologo /c /O2 /EHsc /MT /GR /std:c++17 /DNDEBUG /DUNICODE /D_UNICODE /DWIN32 /D_WINDOWS ^
 /D_CRT_SECURE_NO_WARNINGS /D_WINSOCK_DEPRECATED_NO_WARNINGS /wd4996 /wd4267 /wd4244 ^
 /I sdk /I sdk\pfc /I sdk\foobar2000 /Fo%OBJDIR%\

rem Compile pfc EXCEPT pfc-fb2k-hooks.cpp. The SDK ships "Release FB2K" configs
rem that exclude exactly this file so crashHook / winFormatSystemErrorMessageHook
rem resolve to shared.dll instead (see pfc\suppress_fb2k_hooks.h) - compiling it
rem here would collide with SDK\utility.cpp (LNK2005).
echo [1/4] compiling pfc (%ARCH%) ...
for %%f in (sdk\pfc\*.cpp) do (
  if /I not "%%~nxf"=="pfc-fb2k-hooks.cpp" if /I not "%%~nxf"=="nix-objects.cpp" (
    cl %CFLAGS% "%%f" || ( echo PFC_FAILED on %%f & exit /b 1 )
  )
)

echo [2/4] compiling foobar2000 SDK ...
cl %CFLAGS% sdk\foobar2000\SDK\*.cpp
if errorlevel 1 ( echo SDK_FAILED & exit /b 1 )

echo [3/4] compiling component client + our component ...
cl %CFLAGS% sdk\foobar2000\foobar2000_component_client\component_client.cpp
if errorlevel 1 ( echo CLIENT_FAILED & exit /b 1 )
cl %CFLAGS% foo_tunethathue.cpp
if errorlevel 1 ( echo COMPONENT_FAILED & exit /b 1 )

rc.exe /nologo /fo %OBJDIR%\foo_tunethathue.res foo_tunethathue.rc
if errorlevel 1 ( echo RC_FAILED & exit /b 1 )

echo [4/4] linking (%MACHINE%) ...
link /nologo /DLL /MACHINE:%MACHINE% /OUT:foo_tunethathue-%ARCH%.dll ^
  %OBJDIR%\*.obj %OBJDIR%\foo_tunethathue.res ^
  sdk\foobar2000\shared\%SHAREDLIB% ^
  ws2_32.lib user32.lib shell32.lib ole32.lib oleaut32.lib advapi32.lib comctl32.lib gdi32.lib uuid.lib
if errorlevel 1 ( echo LINK_FAILED & exit /b 1 )

echo BUILD_OK foo_tunethathue-%ARCH%.dll
