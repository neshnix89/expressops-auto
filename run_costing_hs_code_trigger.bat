@echo off
REM ============================================================
REM  Costing / HS Code trigger + reminder loop - LIVE run.
REM  Portable: uses tmoghanan's explicit Python path when present,
REM  otherwise falls back to the py launcher / python on PATH, so
REM  the SAME file works on a backup laptop with a different user.
REM  cd's to its own folder (%~dp0), so location doesn't matter.
REM  Double-click to run once, or point Task Scheduler at this file.
REM ============================================================
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
  where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
)
%PY% -m tasks.costing_hs_code_trigger.main --live >> logs\costing_hs_code_trigger.log 2>&1
