@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

set "PYTHON_EXE="
set "PYTHON_CMD="
if exist "%USERPROFILE%\.local\share\pianotrans-tools-env\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\.local\share\pianotrans-tools-env\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE py -3.11 -c "pass" >nul 2>nul && set "PYTHON_CMD=py -3.11"
if not defined PYTHON_EXE if not defined PYTHON_CMD python -c "pass" >nul 2>nul && set "PYTHON_CMD=python"

if defined PYTHON_EXE (
  "%PYTHON_EXE%" clean_midi_rules.py
) else if defined PYTHON_CMD (
  %PYTHON_CMD% clean_midi_rules.py
) else (
  echo Python 3 was not found.
  echo Install Python 3.11, or create the shared environment with:
  echo   py -3.11 -m venv "%USERPROFILE%\.local\share\pianotrans-tools-env"
  echo Then double-click this file again.
  pause
  exit /b 1
)
pause
