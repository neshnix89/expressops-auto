@echo off
REM ============================================================
REM  RE-SEED the Costing/HS Code baseline.
REM
REM  Marks EVERY container that is ready RIGHT NOW as "already
REM  handled" so it is never triggered. Use this before going live
REM  so containers whose HS Code / costing was already requested
REM  (e.g. via the MO trigger comment) are not asked twice.
REM
REM  POSTS NOTHING - read-only. Safe to run any time.
REM  ADDITIVE - keeps everything already in the baseline and adds
REM  whatever is ready today, so re-running never un-protects a
REM  container.
REM
REM  Run this on the MONDAY you want the fresh start, BEFORE the
REM  09:30 scheduled run. Afterwards only containers that become
REM  ready from that point on will ever be commented.
REM ============================================================
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
  where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
)
echo Re-seeding baseline (nothing will be posted to JIRA)...
echo.
%PY% -m tasks.costing_hs_code_trigger.main --live --seed-baseline
echo.
echo Baseline file: outputs\costing_hs_code_trigger_baseline.json
echo Send that file to the backup laptop so it skips the same containers.
pause
