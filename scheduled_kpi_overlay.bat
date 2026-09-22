@echo off
REM ============================================================
REM  SCHEDULED (non-interactive) KPI OVERLAY refresh.
REM  This is what the daily 09:30 Task Scheduler job runs — it
REM  syncs the latest code from GitHub main, cleans config, then
REM  computes Singapore + Trutnov container/WP KPIs and uploads
REM  kpi_cache.json to Confluence page 572629046 (the Tampermonkey
REM  overlay reads it). No popups; everything is appended to
REM  logs\kpi_overlay_scheduled.log.
REM  Replaces the legacy LiveKPI_Overlay\live_kpi.py job.
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
REM `where python` also matches the Microsoft Store alias stub under

REM %LOCALAPPDATA%\Microsoft\WindowsApps. That stub is not Python: running it

REM only prints "Python was not found; run without arguments to install from

REM the Microsoft Store" and fails. Filter it out INSIDE the for, so the

REM fallback can never hand back a path that breaks every call after it.

if not defined PY for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i /v /c:"\WindowsApps"') do if not defined PY set "PY=%%i"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

if not defined PY (
    echo [%DATE% %TIME%] ERROR: python.exe not found - set EXPRESSOPS_PY >> logs\kpi_overlay_scheduled.log
    endlocal
    exit /b 9
)
echo [%DATE% %TIME%] --- scheduled run starting (%PY%) --- >> logs\kpi_overlay_scheduled.log

"%PY%" scripts\sync_from_github.py       >> logs\kpi_overlay_scheduled.log 2>&1
"%PY%" scripts\clean_config.py           >> logs\kpi_overlay_scheduled.log 2>&1
"%PY%" -m tasks.kpi_overlay.main --live  >> logs\kpi_overlay_scheduled.log 2>&1
REM Propagate the task's real outcome: Task Scheduler only shows what we exit
REM with, and a silent 0 is what hid this failure in the first place.
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] --- scheduled run finished rc=%RC% --- >> logs\kpi_overlay_scheduled.log
endlocal & exit /b %RC%
