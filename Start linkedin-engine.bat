@echo off
rem Double-click to start linkedin-engine's web UI (Windows). Installs uv if needed, syncs the
rem dependencies and opens the search page in your browser. Close this window to stop it.
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv ^(Python package manager^)...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Preparing linkedin-engine (the first time this can take a minute)...
uv sync --quiet
if errorlevel 1 goto failed
uv run engine ui
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Something went wrong. See the messages above.
pause
