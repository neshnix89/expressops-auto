@echo off
REM ============================================================
REM  ONE-TIME setup: run the MO Ref-order-no monitor every 30 min
REM  during working hours.
REM
REM  Creates ONE Task Scheduler job that repeats every 30 minutes
REM  for 9 hours starting 08:00 (i.e. 08:00-17:00, matching the
REM  working-hours dwell window). Re-running overwrites it (/F),
REM  so it is safe to run again to change the cadence.
REM
REM  Runs as the logged-on user — you must be signed in. That is
REM  required anyway for the Webex desktop transport to type.
REM
REM  BEFORE scheduling, do a manual live run on ONE container and
REM  check the JIRA result:
REM    python -m tasks.mo_ref_order_monitor.main --live --container NPIOTHER-5589
REM ============================================================
setlocal
set "BAT=C:\Users\tmoghanan\Documents\AI\expressops-auto\run_mo_ref_order_monitor.bat"
set "TN=MO_RefOrder_Monitor"

echo Creating 30-minute repeating job for the MO Ref-order-no monitor...
echo   -^> %BAT%
echo.

schtasks /Create /TN "%TN%" /TR "%BAT%" /SC DAILY /ST 08:00 /RI 30 /DU 0009:00 /F

echo.
echo Scheduled job:
schtasks /Query /TN "%TN%" /FO LIST | findstr /I "TaskName Next Status"
echo.
echo Done. Runs every 30 min from 08:00 to 17:00 (while you are logged in).
echo.
echo To pause / resume / remove:
echo   schtasks /Change /TN "%TN%" /DISABLE
echo   schtasks /Change /TN "%TN%" /ENABLE
echo   schtasks /Delete /TN "%TN%" /F
echo.
echo Log: %INSTALL%\logs\mo_ref_order_monitor_run.log
pause
endlocal
