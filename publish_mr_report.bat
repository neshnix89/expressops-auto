@echo off
REM ============================================================
REM  MR STATUS REPORT — LIVE PUBLISH (company laptop).
REM  *** THIS WRITES TO Confluence page 560866215. ***
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA/EDM/Confluence and PUBLISHES the page
REM    4. opens the log
REM  Uses EDMAdmin.exe for the EDM/PRSG step when available.
REM  This is the same action the daily scheduled job performs.
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

echo [3/4] Running MR report (LIVE - PUBLISHING to Confluence)...
echo        runner = %RUN%
"%RUN%" -m tasks.mr_status_report.main --live

echo [4/4] Opening log...
start "" notepad "logs\mr_status_report.log"
endlocal
