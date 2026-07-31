@echo off
REM ============================================================
REM  DOUBLE-CLICK ME.
REM
REM  Writes a full health report for the MO Ref-order-no monitor
REM  to logs\diagnose.txt and opens it in Notepad, ready to paste.
REM
REM  Covers: code freshness, what config.yaml actually parses to,
REM  shared state reachability, the scheduled task, and a READ-ONLY
REM  dry run. Nothing is written to JIRA, M3 or Webex.
REM
REM  Secrets are never included - tokens and the Webex space link
REM  are reported as "SET (n chars)" only, so the output is safe
REM  to paste.
REM ============================================================
setlocal
cd /d "%~dp0"

REM Find a usable Python: the known install first, then PATH.
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py"

if not exist logs mkdir logs
set "OUT=logs\diagnose.txt"
set PYTHONIOENCODING=utf-8

echo Collecting diagnostics... this takes about a minute.
echo.

"%PY%" scripts\diagnose.py > "%OUT%" 2>&1
"%PY%" -m tasks.mo_ref_order_monitor.main --live --dry-run >> "%OUT%" 2>&1

echo Done. Opening %OUT%
start notepad "%OUT%"
endlocal
