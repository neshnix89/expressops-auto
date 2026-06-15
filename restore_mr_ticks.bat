@echo off
REM ============================================================
REM  ONE-TIME restore + publish.
REM  *** WRITES to Confluence page 560866215. ***
REM  Run this ONCE to bring the Status column back AND restore the
REM  tick-boxes your colleague made (read from page history). Any
REM  container that was ticked moves to COMPLETED MR.
REM  The DAILY scheduled job does NOT do this — recovery is one-off.
REM  After it works once, you don't need this bat again.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

echo [1/3] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
echo [2/3] Cleaning config.yaml...
"%PY%" scripts\clean_config.py
echo [3/3] Restoring ticks + publishing (LIVE)...
"%PY%" -m tasks.mr_status_report.main --live --recover-ticks

echo Opening log...
start "" notepad "logs\mr_status_report.log"
endlocal
