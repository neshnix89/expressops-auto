@echo off
REM ============================================================
REM  MR STATUS REPORT — DRY-RUN preview (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA/EDM/Confluence and BUILDS the page,
REM       but does NOT publish (safe preview)
REM    4. opens the log for you to copy-paste back to Claude
REM  Uses EDMAdmin.exe for the EDM/PRSG step when available.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "EDM=C:\Users\tmoghanan\EDMAdmin.exe"
if exist "%EDM%" ( set "RUN=%EDM%" ) else ( set "RUN=%PY%" )
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8

echo [1/4] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 echo [WARN] sync failed - running existing local copy.

echo [2/4] Cleaning config.yaml...
"%PY%" scripts\clean_config.py

echo [3/4] Running MR report (LIVE data, DRY-RUN - no Confluence write)...
echo        runner = %RUN%
"%RUN%" -m tasks.mr_status_report.main --live --dry-run

echo [4/4] Opening log...
start "" notepad "logs\mr_status_report.log"
endlocal
