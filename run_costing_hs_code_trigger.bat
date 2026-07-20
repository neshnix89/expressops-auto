@echo off
REM ============================================================
REM  Costing / HS Code trigger + reminder loop — LIVE run.
REM  Scans SG SMT PCBA containers, posts the trigger comment on
REM  newly-ready ones, and chases 2-working-day reminders until
REM  costing (kloo, yuhuang) + HS Code (fpangilina) reply Done.
REM  Backlog seeded via --seed-baseline is skipped automatically.
REM  Double-click to run once, or point Task Scheduler at this file.
REM ============================================================
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe -m tasks.costing_hs_code_trigger.main --live >> logs\costing_hs_code_trigger.log 2>&1
