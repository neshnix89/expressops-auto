@echo off
REM ============================================================
REM  KPI OVERLAY — DRY-RUN preview (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub main (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA and COMPUTES Singapore + Trutnov KPIs,
REM       but does NOT write or upload the cache (safe preview)
REM    4. opens the log so you can copy-paste it back to Claude
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8

echo [1/4] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 echo [WARN] sync failed - running existing local copy.

echo [2/4] Cleaning config.yaml...
"%PY%" scripts\clean_config.py

echo [3/4] Running KPI overlay (LIVE data, DRY-RUN - no Confluence write)...
"%PY%" -m tasks.kpi_overlay.main --live --dry-run --verbose

echo [4/4] Opening log...
start "" notepad "logs\kpi_overlay.log"
endlocal
