@echo off
REM ============================================================
REM  FIND THE KPI WAREHOUSE DSN (company laptop) — read-only.
REM
REM  The BI team gave credentials for sync_user but no database
REM  host. This machine already has 11 ODBC DSNs configured, two
REM  of them named DWHSALES / DWHWIS, so the warehouse may well
REM  be reachable from here already and just needs pointing at.
REM
REM  This logs in to each ORACLE DSN as sync_user, ONCE each, and
REM  asks the data dictionary whether Fact_pm_npi_wc_kpi and
REM  friends are visible there. It only ever SELECTs.
REM
REM  WARNING, read this before running:
REM    A wrong password counts toward Oracle's failed-login limit
REM    on each database it is tried against. This makes exactly
REM    one attempt per DSN and stops the moment it sees ORA-28000
REM    (account locked). To test a single DSN instead, run:
REM
REM      <python> scripts\kpi_warehouse_discovery.py --try-dsns --dsn DWHWIS
REM ============================================================
setlocal
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

if not defined PY (
    echo [ERROR] python.exe not found. Install Python 3.12, or set EXPRESSOPS_PY.
    pause
    exit /b 9
)

echo.
echo This will attempt ONE login per Oracle DSN as sync_user.
echo It stops immediately if Oracle reports the account is locked.
echo.
choice /c YN /m "Continue"
if errorlevel 2 (
    echo Cancelled - nothing was attempted.
    pause
    exit /b 0
)

echo.
echo Probing...
"%PY%" scripts\kpi_warehouse_discovery.py --try-dsns
set "RC=%ERRORLEVEL%"

echo.
echo Opening the newest report...
for /f "delims=" %%F in ('dir /b /o-d "outputs\kpi_discovery\discovery_*.txt" 2^>nul') do (
    start "" notepad "outputs\kpi_discovery\%%F"
    goto :done
)
echo [WARN] no report file found - read the console output above.
:done
echo.
echo The report contains no password and is safe to send to IT or BI.
endlocal & exit /b %RC%
