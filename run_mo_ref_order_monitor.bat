@echo off
REM ============================================================
REM  Poll M3 'Ref order no' (VHRORN) for active MOs and update the
REM  JIRA Work Container tracking table + working-hours dwell.
REM  Webex alerts fire only when the IS issue flag changes.
REM
REM  Runs from the production checkout.
REM
REM  PILOT SCOPE lives in config.yaml -> mo_ref_order_monitor.pilot_containers
REM  (config.yaml is gitignored, so edits there survive sync_now; an edit to
REM  THIS file would be overwritten by the next sync). Empty that list to go
REM  fleet-wide. Set PILOT below only to override it for a one-off run.
REM ============================================================
setlocal
set "INSTALL=C:\Users\tmoghanan\Documents\AI\expressops-auto"
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "PILOT="

cd /d "%INSTALL%"
set PYTHONIOENCODING=utf-8
"%PY%" -m tasks.mo_ref_order_monitor.main --live %PILOT% >> logs\mo_ref_order_monitor_run.log 2>&1
endlocal
