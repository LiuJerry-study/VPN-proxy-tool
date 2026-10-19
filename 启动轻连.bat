@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Python 3 was not found. Please install Python 3.10 or newer.
  pause
  exit /b 1
)
start "" pythonw.exe "%~dp0app.py"
exit /b 0
