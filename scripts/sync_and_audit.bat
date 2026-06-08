@echo off
:: ============================================================
:: ExpressOPS - Sync + Container Template Audit (one click)
::
:: 1. Pulls the latest code from GitHub main (no git needed)
:: 2. Runs the container_template_audit scan against LIVE JIRA
::    in --dry-run mode -> READS live data, writes NOTHING
::    (no Confluence publish, no JIRA comments)
:: 3. Writes the full detailed findings to audit_output.txt
::    and opens it so you can copy-paste the results back.
::
:: NOTE: keep this file unchanged once deployed. The sync step
:: overwrites it with the identical copy from GitHub, which is
:: safe only as long as the bytes match.
:: ============================================================

set "PYTHONIOENCODING=utf-8"
set "PYTHONWARNINGS=ignore"
set "ROOT=C:\Users\tmoghanan\Documents\AI\expressops-auto"
set "PYTHON=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "OUT=%ROOT%\audit_output.txt"

echo.
echo [1/2] Syncing from GitHub main...
"%PYTHON%" "%ROOT%\scripts\sync_from_github.py"
if errorlevel 1 (
    echo [ERROR] Sync failed. Check your network and try again.
    pause
    exit /b 1
)

echo.
echo [2/2] Running audit scan (LIVE read, dry-run, no writes)...
cd /d "%ROOT%"
"%PYTHON%" tasks\container_template_audit\batch.py scan --live --dry-run > "%OUT%" 2>&1

echo.
echo ============================================================
echo  Done. Detailed findings written to:
echo    %OUT%
echo  Opening it now - copy-paste the contents back to Claude.
echo ============================================================
echo.
notepad "%OUT%"
