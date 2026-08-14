@echo off
REM ============================================================
REM  MR STATUS REPORT — LIVE PUBLISH (company laptop).
REM  *** THIS WRITES TO Confluence page 560866215. ***
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA/EDM/Confluence and PUBLISHES the page
REM    4. opens the log
REM  EDM is queried under EDMAdmin.exe automatically (core/edm.py).
REM  If EDM is unavailable the run REFUSES to publish (so PRSG is
REM  never blanked) — fix EDMAdmin.exe or use --allow-no-edm.
REM  This is the same action the daily scheduled job performs.
REM ============================================================
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PY=%EXPRESSOPS_PY%"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
REM Last resort: whatever `python` / `py` resolves to on PATH. Without this a
REM machine with Python outside the usual install spots (e.g. C:\tools\python3)
REM looks like it has no Python at all.
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

echo [3/4] Running MR report (LIVE - PUBLISHING to Confluence)...
"%PY%" -m tasks.mr_status_report.main --live

echo [4/4] Opening log...
start "" notepad "logs\mr_status_report.log"
endlocal
