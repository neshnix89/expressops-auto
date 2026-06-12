@echo off
REM ============================================================
REM  MR STATUS REPORT — LIVE PUBLISH (company laptop).
REM  *** THIS WRITES TO Confluence page 560866215. ***
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. cleans config.yaml (strip UTF-8 BOM if present)
REM    3. reads live JIRA/EDM/Confluence and PUBLISHES the page
REM    4. opens the log
REM  EDM is queried under EDMAdmin.exe automatically (core/edm.py).
REM  If EDM is unavailable the run REFUSES to publish (so PRSG is
REM  never blanked) — fix EDMAdmin.exe or use --allow-no-edm.
REM  This is the same action the daily scheduled job performs.
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

echo [3/4] Running MR report (LIVE - PUBLISHING to Confluence)...
"%PY%" -m tasks.mr_status_report.main --live

echo [4/4] Opening log...
start "" notepad "logs\mr_status_report.log"
endlocal
