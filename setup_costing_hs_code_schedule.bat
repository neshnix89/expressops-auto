@echo off
REM ============================================================
REM  ONE-TIME setup: schedule the Costing / HS Code trigger to
REM  run 3x per working day (09:30, 12:45, 16:00) so it checks
REM  for newly-ready containers and posts / reminds automatically.
REM
REM  Creates three daily Task Scheduler jobs pointing at
REM  run_costing_hs_code_trigger.bat. Re-running this file just
REM  overwrites them (/F), so it is safe to run again to change
REM  the times. Runs as the logged-on user (needs you signed in).
REM
REM  Double-click ONCE. IMPORTANT: seed the backlog first with
REM     python -m tasks.costing_hs_code_trigger.main --live --seed-baseline
REM  or the first scheduled run will comment on existing tickets.
REM ============================================================
setlocal
set "BAT=C:\Users\tmoghanan\Documents\AI\expressops-auto\run_costing_hs_code_trigger.bat"

echo Creating 3 daily jobs for Costing/HS Code trigger...
echo   -> %BAT%
echo.

schtasks /Create /TN "CostingHSCode_0930" /TR "%BAT%" /SC DAILY /ST 09:30 /F
schtasks /Create /TN "CostingHSCode_1245" /TR "%BAT%" /SC DAILY /ST 12:45 /F
schtasks /Create /TN "CostingHSCode_1600" /TR "%BAT%" /SC DAILY /ST 16:00 /F

echo.
echo Scheduled jobs:
schtasks /Query /TN "CostingHSCode_0930" /FO LIST | findstr /I "TaskName Next Status"
schtasks /Query /TN "CostingHSCode_1245" /FO LIST | findstr /I "TaskName Next Status"
schtasks /Query /TN "CostingHSCode_1600" /FO LIST | findstr /I "TaskName Next Status"
echo.
echo Done. Runs 09:30, 12:45 and 16:00 every day (while you are logged in).
echo To remove them later:
echo   schtasks /Delete /TN "CostingHSCode_0930" /F
echo   schtasks /Delete /TN "CostingHSCode_1245" /F
echo   schtasks /Delete /TN "CostingHSCode_1600" /F
pause
endlocal
