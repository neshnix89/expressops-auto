@echo off
REM ============================================================
REM  MR STATUS REPORT — DRY-RUN preview (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA/EDM/Confluence and BUILDS the page,
REM       but does NOT publish (safe preview)
REM    4. opens the log for you to copy-paste back to Claude
REM  EDM is queried under EDMAdmin.exe automatically (core/edm.py),
REM  so this runs under the normal Python.
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
REM `where python` also matches the Microsoft Store alias stub under

REM %LOCALAPPDATA%\Microsoft\WindowsApps. That stub is not Python: running it

REM only prints "Python was not found; run without arguments to install from

REM the Microsoft Store" and fails. Filter it out INSIDE the for, so the

REM fallback can never hand back a path that breaks every call after it.

if not defined PY for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i /v /c:"\WindowsApps"') do if not defined PY set "PY=%%i"
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

echo [3/4] Running MR report (LIVE data, DRY-RUN - no Confluence write)...
"%PY%" -m tasks.mr_status_report.main --live --dry-run

echo [4/4] Opening log...
start "" notepad "logs\mr_status_report.log"
endlocal
