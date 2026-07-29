@echo off
rem Build foo_tunethathue.dll (foobar2000 component, x64) with MSVC.
rem
rem Route "direct cl.exe": compile the SDK sources we actually need (pfc +
rem foobar2000/SDK + the component client) straight into the DLL, then link the
rem prebuilt shared-x64.lib that ships with the SDK. This deliberately avoids
rem the SDK's vcxproj files (which target older toolsets) and libPPUI/helpers
rem (which need ATL - not part of the Build Tools C++ workload).
rem
rem Run tools\fetch_foobar_sdk.ps1 first to populate sdk\.
setlocal enabledelayedexpansion
set VCVARS=D:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat
call "%VCVARS%" x64 >nul
if errorlevel 1 ( echo VCVARS_FAILED & exit /b 1 )

cd /d "%~dp0"
if not exist sdk\pfc ( echo SDK_MISSING - run tools\fetch_foobar_sdk.ps1 & exit /b 1 )

set OBJDIR=build_obj
if not exist %OBJDIR% mkdir %OBJDIR%

rem The SDK is C++ with exceptions + RTTI; UNICODE matches how foobar builds it.
rem The SDK requires C++17 (pfc-lite.h enforces it).
set CFLAGS=/nologo /c /O2 /EHsc /MT /GR /std:c++17 /DUNICODE /D_UNICODE /DWIN32 /D_WINDOWS ^
 /D_CRT_SECURE_NO_WARNINGS /D_WINSOCK_DEPRECATED_NO_WARNINGS /wd4996 /wd4267 /wd4244 ^
 /I sdk /I sdk\pfc /I sdk\foobar2000 /Fo%OBJDIR%\

rem Compile pfc EXCEPT pfc-fb2k-hooks.cpp. The SDK ships "Release FB2K" configs
rem that exclude exactly this file so crashHook / winFormatSystemErrorMessageHook
rem resolve to shared.dll instead (see pfc\suppress_fb2k_hooks.h) - compiling it
rem here would collide with SDK\utility.cpp (LNK2005).
echo [1/4] compiling pfc ...
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

echo [4/4] linking ...
link /nologo /DLL /MACHINE:X64 /OUT:foo_tunethathue.dll ^
  %OBJDIR%\*.obj %OBJDIR%\foo_tunethathue.res ^
  sdk\foobar2000\shared\shared-x64.lib ^
  ws2_32.lib user32.lib shell32.lib ole32.lib oleaut32.lib advapi32.lib comctl32.lib gdi32.lib uuid.lib
if errorlevel 1 ( echo LINK_FAILED & exit /b 1 )

echo BUILD_OK
