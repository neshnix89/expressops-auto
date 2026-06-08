@echo off
REM ============================================================
REM  One-click CONTAINER TEMPLATE AUDIT runner (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. runs the audit scan against live JIRA with writes
REM       HARD-BLOCKED (read-only guard + dry-run)
REM    3. opens the output for you to copy-paste back to Claude
REM  Read-only by construction — see scripts\readonly_guard.py.
REM  Publishing to Confluence is a separate, deliberate step:
REM    scripts\run_audit_batch.bat scan --live
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto

echo [1/4] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 (
    echo [ERROR] sync failed - check network. Running existing local copy.
)

echo [2/4] Cleaning config.yaml (strip UTF-8 BOM if present)...
"%PY%" scripts\clean_config.py

echo [3/4] Running audit scan (READ-ONLY guard active, dry-run)...
"%PY%" -m scripts.run_audit

echo [4/4] Opening output...
start "" notepad "outputs\_audit_latest.txt"
endlocal
