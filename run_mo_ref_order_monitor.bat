@echo off
REM ============================================================
REM  Poll M3 'Ref order no' (VHRORN) for active MOs and update the
REM  JIRA Work Container tracking table + working-hours dwell.
REM  Webex alerts fire only when the IS issue flag changes.
REM
REM  Runs from the DEV checkout during the pilot; switch INSTALL to
REM  the main folder once this task is merged to main.
REM
REM  PILOT: --container limits writes to the listed containers.
REM  Remove that argument to go fleet-wide.
REM ============================================================
setlocal
set "INSTALL=C:\Users\tmoghanan\Documents\AI\expressops-auto-dev"
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "PILOT=--container NPIOTHER-5589,NPIOTHER-5322,NPIOTHER-5791"

cd /d "%INSTALL%"
set PYTHONIOENCODING=utf-8
"%PY%" -m tasks.mo_ref_order_monitor.main --live %PILOT% >> logs\mo_ref_order_monitor_run.log 2>&1
endlocal
