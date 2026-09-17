@echo off
REM Usage: run_mms130_status_change.bat "C:\path\list.xlsx" [--apply] [--limit N] [--discover]
REM Without --apply this is a DRY RUN (nothing is changed in M3).
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe -m tasks.mms130_status_change.main --live --input %*
pause
