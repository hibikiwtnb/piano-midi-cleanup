@echo off
setlocal
cd /d "%~dp0..\sound_sources\coopiano"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo Visual Studio C++ Build Tools were not found.
  exit /b 1
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSROOT=%%i"
if not defined VSROOT exit /b 1
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
if not exist build mkdir build
if not exist native mkdir native
cl /nologo /utf-8 /O2 /std:c++17 /EHsc /MT /LD playback_bridge.cpp /Fo:build\playback_bridge.obj /Fe:native\coopiano.dll /link /IMPLIB:build\coopiano.lib
exit /b %errorlevel%
