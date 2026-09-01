@echo off
REM ============================================================
REM  KPI VALIDATION (company laptop) — read-only.
REM  Double-click this AFTER run_kpi_discovery.bat has found a route.
REM  It:
REM    1. syncs the latest code from GitHub main (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. computes today's KPIs BOTH ways — from JIRA and from the
REM       Tableau fact tables — and diffs them container by container
REM    4. opens the report so you can paste it back to Claude
REM
REM  Nothing is uploaded; the live overlay attachment is untouched.
REM  A markdown copy is written next to the JSON for Confluence.
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

echo [3/4] Comparing the overlay's KPIs against Tableau's...
%PY% scripts\validate_kpi_vs_tableau.py --live --md

echo [4/4] Opening the newest report...
for /f "delims=" %%F in ('dir /b /o-d "outputs\kpi_validation\validation_*.md" 2^>nul') do (
    start "" notepad "outputs\kpi_validation\%%F"
    goto :done
)
echo [WARN] no report file found - read the console output above.
:done
endlocal
pause
