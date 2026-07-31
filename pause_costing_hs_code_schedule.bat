@echo off
REM ============================================================
REM  PAUSE / RESUME the 3 daily Costing-HS Code scheduled jobs.
REM
REM    pause_costing_hs_code_schedule.bat          -> disable all 3
REM    pause_costing_hs_code_schedule.bat resume   -> enable all 3
REM
REM  Use this if you want a clean start on a chosen day: disable
REM  now, re-seed the baseline on that morning, then resume.
REM  Affects the PRIMARY jobs (09:30 / 12:45 / 16:00).
REM ============================================================
setlocal
set "MODE=DISABLE"
set "WORD=paused"
if /I "%~1"=="resume" set "MODE=ENABLE" & set "WORD=resumed"

for %%T in (CostingHSCode_0930 CostingHSCode_1245 CostingHSCode_1600) do (
  schtasks /Change /TN "%%T" /%MODE% >nul 2>&1 && (
    echo   %%T  %WORD%
  ) || (
    echo   %%T  NOT FOUND ^(not scheduled on this laptop^)
  )
)
echo.
echo Jobs %WORD%.
if /I not "%~1"=="resume" echo Run with:  pause_costing_hs_code_schedule.bat resume   to turn them back on.
pause
endlocal
