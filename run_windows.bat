@echo off
REM Helper to start the project on Windows. This attempts to use WSL or Git Bash to run the project's run.sh
setlocal
where wsl.exe >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via WSL...
  wsl -e bash -lc "cd \"\" && ./run.sh"
  exit /b 0
)
where bash >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Starting via Git Bash...
  bash -lc "cd \"/Users/lfranzke/Documents/ZHdK/11_Physical Computing Lab/Technology/LLM_embodiments\" && ./run.sh"
  exit /b 0
)
echo No WSL or Bash found in PATH. Please run the project from WSL or Git Bash manually.
pause
