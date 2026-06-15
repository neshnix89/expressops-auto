@echo off
REM ============================================================
REM  SCHEDULED (non-interactive) MR Status Report publish.
REM  This is what the daily Task Scheduler job runs — it syncs
REM  the latest code, then publishes to Confluence. No popups;
REM  everything is appended to logs\mr_scheduled.log.
REM  EDM is queried under EDMAdmin.exe automatically (core/edm.py).
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

"%PY%" scripts\sync_from_github.py  >> logs\mr_scheduled.log 2>&1
"%PY%" scripts\clean_config.py      >> logs\mr_scheduled.log 2>&1
"%PY%" -m tasks.mr_status_report.main --live >> logs\mr_scheduled.log 2>&1
endlocal
