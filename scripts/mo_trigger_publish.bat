@echo off
REM ============================================================
REM  DELIBERATE LIVE PUBLISH  -  mo_trigger_comment
REM  This WRITES the staging table to the Confluence page.
REM  Run it ONLY when you intend to update the page. This is a
REM  separate, on-purpose action - it is NOT part of the
REM  read-only probe loop (run_probe.bat).
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto

echo [1/3] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py

echo [2/3] Cleaning config.yaml (strip UTF-8 BOM if present)...
"%PY%" scripts\clean_config.py

echo [3/3] Publishing mo_trigger to Confluence (LIVE WRITE)...
"%PY%" -m tasks.mo_trigger_comment.main run --live --publish

echo.
echo Done. Re-run run_probe.bat to confirm the page's new version/Run time.
pause
endlocal
