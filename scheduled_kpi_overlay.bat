@echo off
REM ============================================================
REM  SCHEDULED (non-interactive) KPI OVERLAY refresh.
REM  This is what the daily 09:30 Task Scheduler job runs — it
REM  syncs the latest code from GitHub main, cleans config, then
REM  computes Singapore + Trutnov container/WP KPIs and uploads
REM  kpi_cache.json to Confluence page 572629046 (the Tampermonkey
REM  overlay reads it). No popups; everything is appended to
REM  logs\kpi_overlay_scheduled.log.
REM  Replaces the legacy LiveKPI_Overlay\live_kpi.py job.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

"%PY%" scripts\sync_from_github.py       >> logs\kpi_overlay_scheduled.log 2>&1
"%PY%" scripts\clean_config.py           >> logs\kpi_overlay_scheduled.log 2>&1
"%PY%" -m tasks.kpi_overlay.main --live  >> logs\kpi_overlay_scheduled.log 2>&1
endlocal
