@echo off
REM ============================================================
REM  ONE-TIME: create EDMAdmin.exe + test EDM connectivity.
REM  Double-click this once. It:
REM    1. syncs the latest code from GitHub
REM    2. copies python.exe -> <python install dir>\EDMAdmin.exe
REM       (the renamed exe that passes the Oracle logon trigger)
REM    3. runs a single known PT->PRSG query to confirm EDM works
REM    4. opens the short result for you to paste back to Claude
REM  Read-only against EDM (one SELECT). Safe to re-run.
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
if not exist logs mkdir logs

echo [1/2] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py

echo [2/2] Creating EDMAdmin.exe and testing EDM...
"%PY%" scripts\setup_edmadmin.py > logs\setup_edmadmin.txt 2>&1
type logs\setup_edmadmin.txt

start "" notepad "logs\setup_edmadmin.txt"
endlocal
