@echo off
REM ============================================================
REM  KPI OVERLAY — DRY-RUN preview (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub main (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA and COMPUTES Singapore + Trutnov KPIs,
REM       but does NOT write or upload the cache (safe preview)
REM    4. opens the log so you can copy-paste it back to Claude
REM ============================================================
setlocal
REM Repo root = the folder this .bat lives in, NOT a hardcoded profile path.
REM Hardcoding "C:\Users\tmoghanan\..." made this file a no-op on any other
REM machine: the cd failed, python.exe was not found, and the task still exited
REM 0 — a scheduled job showing result=0 while publishing nothing. Observed on
REM the second laptop, where the KPI pills never refreshed.
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

set "PY=%EXPRESSOPS_PY%"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
set PYTHONIOENCODING=utf-8

if not defined PY (
    echo [ERROR] python.exe not found. Install Python 3.12, or set EXPRESSOPS_PY.
    pause
    exit /b 9
)

echo [1/4] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 echo [WARN] sync failed - running existing local copy.

echo [2/4] Cleaning config.yaml...
"%PY%" scripts\clean_config.py

echo [3/4] Running KPI overlay (LIVE data, DRY-RUN - no Confluence write)...
"%PY%" -m tasks.kpi_overlay.main --live --dry-run --verbose

echo [4/4] Opening log...
start "" notepad "logs\kpi_overlay.log"
endlocal
