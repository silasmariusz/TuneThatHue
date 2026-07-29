@echo off
rem Build TuneThatHue-SoundRecorder (x64, MSVC). Called by build-windows.ps1,
rem but works standalone too. /MT so the exe runs on a machine without the
rem Visual C++ redistributable.
setlocal
set VCVARS=D:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvarsall.bat
if not exist "%VCVARS%" (
  for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VCVARS=%%i\VC\Auxiliary\Build\vcvarsall.bat
)
call "%VCVARS%" x64 >nul
if errorlevel 1 ( echo VCVARS_FAILED & exit /b 1 )

cd /d "%~dp0"
rc.exe /nologo /fo tth_soundrecorder.res tth_soundrecorder.rc
if errorlevel 1 ( echo RC_FAILED & exit /b 1 )

cl /nologo /O2 /EHsc /MT /W3 tth_soundrecorder.cpp tth_soundrecorder.res ^
   /link /SUBSYSTEM:WINDOWS /MACHINE:X64 ^
   ws2_32.lib ole32.lib oleaut32.lib shell32.lib user32.lib advapi32.lib ^
   mmdevapi.lib runtimeobject.lib propsys.lib ^
   /OUT:TuneThatHue-SoundRecorder.exe
if errorlevel 1 ( echo COMPILE_FAILED & exit /b 1 )

del /q *.obj *.res 2>nul
echo BUILD_OK
