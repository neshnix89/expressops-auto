@echo off
REM ============================================================
REM  Manual CONTAINER AUDIT - just double-click me.
REM
REM  A window opens and asks for a container key. Paste it,
REM  press Enter, and the audit findings print right there
REM  (and save to outputs\_manual_check.txt). Paste another,
REM  or press Enter on a blank line to quit.
REM
REM  Read-only: it never writes to JIRA or Confluence. It
REM  syncs the latest code from GitHub first.
REM ============================================================
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONWARNINGS=ignore"
set "ROOT=C:\Users\tmoghanan\Documents\AI\expressops-auto"
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"

cd /d "%ROOT%"

echo Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py

REM Interactive prompt loop lives inside batch.py (no keys = ask me).
"%PY%" tasks\container_template_audit\batch.py check --live

echo.
pause
endlocal
