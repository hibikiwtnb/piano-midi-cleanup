@echo off
rem Creates "Piano MIDI Cleanup.lnk" in the repository root. The shortcut opens
rem the post-processing panel with pythonw.exe, so no console window appears.
rem It stores absolute paths, so it can be copied to the Desktop or Start menu;
rem run this again after moving the repository or rebuilding the Python environment.
setlocal
cd /d "%~dp0.."
for %%I in ("%~dp0..") do set "REPO_DIR=%%~fI"

set "PYTHON_EXE="
if exist "%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\.local\share\piano-midi-cleanup-env\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%REPO_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%REPO_DIR%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"

if not defined PYTHON_EXE (
  echo Python 3 was not found.
  echo Install Python 3.11, or create the shared environment with:
  echo   py -3.11 -m venv "%USERPROFILE%\.local\share\piano-midi-cleanup-env"
  echo Then double-click this file again.
  pause
  exit /b 1
)

for %%I in ("%PYTHON_EXE%") do set "PYTHONW_EXE=%%~dpIpythonw.exe"
if not exist "%PYTHONW_EXE%" (
  echo pythonw.exe was not found next to:
  echo   %PYTHON_EXE%
  echo The shortcut needs pythonw.exe to run without a console window.
  pause
  exit /b 1
)

set "LNK_PATH=%REPO_DIR%\Piano MIDI Cleanup.lnk"
set "LNK_ARGS=-X utf8 "%REPO_DIR%\src\postprocess_panel.py""
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK_PATH); $s.TargetPath = $env:PYTHONW_EXE; $s.Arguments = $env:LNK_ARGS; $s.WorkingDirectory = $env:REPO_DIR; $s.Description = 'Piano MIDI Cleanup'; $s.Save()"
if errorlevel 1 (
  echo Failed to create the shortcut.
  pause
  exit /b 1
)

echo Created: %LNK_PATH%
echo Python:  %PYTHONW_EXE%
echo.
echo Double-click it to open the panel. You can also copy it to the Desktop or Start menu.
pause
