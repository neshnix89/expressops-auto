@echo off
REM ============================================================
REM  CONTAINER REPORTERS — Reporter + Resolved date export.
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA (read-only - writes nothing back) using
REM       the same container filter as the KPI overlay
REM    4. opens outputs\container_reporters.csv in Excel
REM
REM  Default: containers that are RESOLVED, all dates.
REM  Pass extra flags straight through, e.g.:
REM      run_container_reporters.bat --since 2026-01-01
REM      run_container_reporters.bat --scope all
REM ============================================================
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PY=%EXPRESSOPS_PY%"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    echo [ERROR] python.exe not found. Install Python 3.12, or set EXPRESSOPS_PY to its path.
    pause
    exit /b 1
)
cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8

echo [1/4] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 echo [WARN] sync failed - running existing local copy.

echo [2/4] Cleaning config.yaml...
"%PY%" scripts\clean_config.py

echo [3/4] Reading live JIRA (read-only)...
"%PY%" -m tasks.container_reporters.main --live --show-jql %*
if errorlevel 1 (
    echo [ERROR] the export failed - see logs\container_reporters.log
    start "" notepad "logs\container_reporters.log"
    pause
    exit /b 1
)

echo [4/4] Opening the CSV...
start "" "outputs\container_reporters.csv"
endlocal
