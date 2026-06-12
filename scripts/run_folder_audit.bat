@echo off
REM ============================================================
REM  One-click FOLDER CLEANUP AUDIT (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest audit script from GitHub (no git needed)
REM    2. lists + classifies files in the expressops-auto folder
REM    3. opens the output for you to copy-paste back
REM  READ-ONLY: nothing is moved or deleted. See scripts\folder_audit.py.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto

echo [1/3] Syncing latest from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 (
    echo [WARN] sync failed - running existing local copy.
)

echo [2/3] Auditing folder (read-only)...
if not exist outputs mkdir outputs
"%PY%" scripts\folder_audit.py > outputs\_folder_audit_latest.txt 2>&1
type outputs\_folder_audit_latest.txt

echo [3/3] Opening output...
start "" notepad "outputs\_folder_audit_latest.txt"
endlocal
