@echo off
REM One-shot: sync latest, then prompt for the NEW JIRA + Confluence PATs and
REM write them into config.yaml (hidden input, no BOM). Run before revoking the
REM old tokens.
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
echo Syncing latest code...
"%PY%" scripts\sync_from_github.py
echo.
"%PY%" scripts\set_tokens.py
echo.
pause
endlocal
