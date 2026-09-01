@echo off
REM ============================================================
REM  KPI WAREHOUSE DISCOVERY (company laptop) — read-only.
REM  Double-click this FIRST, before switching the overlay to Tableau.
REM  It:
REM    1. syncs the latest code from GitHub main (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. works out which route reaches Fact_pm_npi_wc_kpi and friends,
REM       and prints every column of every fact table
REM    4. opens the report so you can paste it back to Claude
REM
REM  Nothing is written to JIRA, Confluence or the database, and the
REM  report never contains the password.
REM
REM  Put the sync_user credentials in config\config.yaml first:
REM      kpi_warehouse:
REM        user: "..."
REM        password: "..."
REM ============================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

REM Python: the standard per-user install, else the py launcher / PATH.
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
    where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
)

echo [1/4] Syncing latest code from GitHub...
%PY% scripts\sync_from_github.py
if errorlevel 1 echo [WARN] sync failed - running existing local copy.

echo [2/4] Cleaning config.yaml...
%PY% scripts\clean_config.py

echo [3/4] Discovering the KPI fact tables...
%PY% scripts\kpi_warehouse_discovery.py

echo [4/4] Opening the newest report...
for /f "delims=" %%F in ('dir /b /o-d "outputs\kpi_discovery\discovery_*.txt" 2^>nul') do (
    start "" notepad "outputs\kpi_discovery\%%F"
    goto :done
)
echo [WARN] no report file found - read the console output above.
:done
endlocal
pause
