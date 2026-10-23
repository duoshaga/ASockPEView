@echo off
setlocal
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0launch.py"
if errorlevel 1 pause
