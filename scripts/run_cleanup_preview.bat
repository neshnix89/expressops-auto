@echo off
REM ============================================================
REM  CLEANUP PREVIEW (company laptop) — DRY RUN, moves NOTHING.
REM  Syncs latest, lists exactly what the cleanup WOULD move,
REM  opens the plan. Review it before running run_cleanup_apply.bat.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto

echo [1/3] Syncing latest from GitHub...
"%PY%" scripts\sync_from_github.py

echo [2/3] Building cleanup plan (DRY RUN - nothing moved)...
if not exist outputs mkdir outputs
"%PY%" -m scripts.folder_cleanup > outputs\_cleanup_preview.txt 2>&1
type outputs\_cleanup_preview.txt

echo [3/3] Opening plan...
start "" notepad "outputs\_cleanup_preview.txt"
endlocal
