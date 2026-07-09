@echo off
REM Helper to start the project on Windows. This attempts to use WSL or Git Bash to run the project's run.sh
setlocal
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
where wsl.exe >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via WSL...
  wsl -e bash -lc "cd \"$(wslpath '%SCRIPT_DIR%')\" && ./run.sh"
  exit /b 0
)
where bash >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via Git Bash...
  bash -lc "cd \"$SCRIPT_DIR\" && ./run.sh"
  exit /b 0
)
echo No WSL or Bash found in PATH. Please run the project from WSL or Git Bash manually.
pause
