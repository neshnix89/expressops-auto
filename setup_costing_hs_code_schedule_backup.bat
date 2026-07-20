@echo off
REM ============================================================
REM  BACKUP LAPTOP setup: schedule Costing/HS Code trigger 3x/day
REM  STAGGERED 15 min after the primary laptop (09:45, 13:00, 16:15)
REM  so when both people work the primary posts first and this backup
REM  sees the marker and skips - no duplicate comments. When the
REM  primary is on leave, this backup still posts.
REM
REM  Double-click ONCE on the BACKUP laptop. Runs as logged-on user.
REM  IMPORTANT: put the shared baseline file in outputs\ FIRST (see
REM  INSTRUCTIONS) or the first run will comment on the old backlog.
REM ============================================================
setlocal
set "RUNBAT=%~dp0run_costing_hs_code_trigger.bat"
echo Creating 3 staggered daily jobs -^> %RUNBAT%
echo.
schtasks /Create /TN "CostingHSCode_BK_0945" /TR "%RUNBAT%" /SC DAILY /ST 09:45 /F
schtasks /Create /TN "CostingHSCode_BK_1300" /TR "%RUNBAT%" /SC DAILY /ST 13:00 /F
schtasks /Create /TN "CostingHSCode_BK_1615" /TR "%RUNBAT%" /SC DAILY /ST 16:15 /F
echo.
echo Done. Backup runs 09:45, 13:00, 16:15 daily (while logged in).
echo Remove later with:
echo   schtasks /Delete /TN "CostingHSCode_BK_0945" /F
echo   schtasks /Delete /TN "CostingHSCode_BK_1300" /F
echo   schtasks /Delete /TN "CostingHSCode_BK_1615" /F
pause
endlocal
