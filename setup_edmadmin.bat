@echo off
REM ============================================================
REM  ONE-TIME: create EDMAdmin.exe + test EDM connectivity.
REM  Double-click this once. It:
REM    1. syncs the latest code from GitHub
REM    2. copies python.exe -> C:\Users\tmoghanan\EDMAdmin.exe
REM       (the renamed exe that passes the Oracle logon trigger)
REM    3. runs a single known PT->PRSG query to confirm EDM works
REM    4. opens the short result for you to paste back to Claude
REM  Read-only against EDM (one SELECT). Safe to re-run.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs

echo [1/2] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py

echo [2/2] Creating EDMAdmin.exe and testing EDM...
"%PY%" scripts\setup_edmadmin.py > logs\setup_edmadmin.txt 2>&1
type logs\setup_edmadmin.txt

start "" notepad "logs\setup_edmadmin.txt"
endlocal
