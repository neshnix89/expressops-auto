@echo off
REM ============================================================
REM  PORTABLE runner for the MO Ref-order-no monitor.
REM  Works from ANY user's checkout (no hardcoded username) — the
REM  repo root is wherever this .bat lives (%~dp0).
REM
REM  Use this on a SECOND laptop. For it to share history with the
REM  first laptop (and not fight over the JIRA tables), config.yaml
REM  on BOTH machines must point state_dir + webex.queue_file at the
REM  SAME shared network path. See setup notes.
REM
REM  If 'py' is not on PATH, set PY to the full python.exe path below.
REM ============================================================
setlocal
set "PY=py"
set "PILOT=--container NPIOTHER-5589,NPIOTHER-5322,NPIOTHER-5791"

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
%PY% -m tasks.mo_ref_order_monitor.main --live %PILOT% >> logs\mo_ref_order_monitor_run.log 2>&1
endlocal
