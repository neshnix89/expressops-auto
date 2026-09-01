@echo off
REM ============================================================
REM  SCHEDULED (non-interactive) MR Status Report publish.
REM  This is what the daily Task Scheduler job runs — it syncs
REM  the latest code, then publishes to Confluence. No popups;
REM  everything is appended to logs\mr_scheduled.log.
REM  EDM is queried under EDMAdmin.exe automatically (core/edm.py).
REM
REM  PORTABLE: no user name is hardcoded, so a second person can
REM  run their own copy as a failover.
REM    repo root  = the folder this .bat lives in (%~dp0)
REM    python.exe = %EXPRESSOPS_PY%, else the usual install spots
REM  Register the schedule with setup_mr_schedule.bat [HH:MM].
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

cd /d "%ROOT%"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

if not defined PY (
    echo [%DATE% %TIME%] ERROR: python.exe not found - set EXPRESSOPS_PY >> logs\mr_scheduled.log
    exit /b 1
)

echo [%DATE% %TIME%] --- scheduled run start ^(%PY%^) >> logs\mr_scheduled.log
"%PY%" scripts\sync_from_github.py  >> logs\mr_scheduled.log 2>&1
"%PY%" scripts\clean_config.py      >> logs\mr_scheduled.log 2>&1
"%PY%" -m tasks.mr_status_report.main --live >> logs\mr_scheduled.log 2>&1
echo [%DATE% %TIME%] --- scheduled run end ^(exit %ERRORLEVEL%^) >> logs\mr_scheduled.log
endlocal
