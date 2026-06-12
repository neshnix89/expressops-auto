@echo off
REM ============================================================
REM  CLEANUP APPLY (company laptop) — MOVES files to the archive.
REM  Moves (never deletes) approved files into
REM    C:\Users\tmoghanan\Documents\AI\expressops-auto-archive\<date>\
REM  Writes a manifest + a restore_<date>.bat so it is reversible.
REM  Run run_cleanup_preview.bat FIRST and review the plan.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto

echo [1/3] Syncing latest from GitHub...
"%PY%" scripts\sync_from_github.py

echo [2/3] Applying cleanup (MOVING files)...
if not exist outputs mkdir outputs
"%PY%" -m scripts.folder_cleanup --apply > outputs\_cleanup_apply.txt 2>&1
type outputs\_cleanup_apply.txt

echo [3/3] Opening result...
start "" notepad "outputs\_cleanup_apply.txt"
endlocal
