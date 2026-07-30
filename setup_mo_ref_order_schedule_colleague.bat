@echo off
REM ============================================================
REM  ONE-TIME setup on the SECOND (colleague's) laptop.
REM  Schedules the MO Ref-order-no monitor every 30 min from 09:15
REM  to 17:00 — deliberately OFFSET from the primary laptop's
REM  08:00/08:30 slots (this fires at :15 and :45) so the two never
REM  run in the same minute and race on JIRA writes / Webex.
REM
REM  Portable: points at run_mo_ref_order_monitor_portable.bat in
REM  the same folder (%~dp0), so it works regardless of username.
REM
REM  PREREQUISITE — do this FIRST or the two laptops will fight:
REM    config.yaml on BOTH machines must set
REM      mo_ref_order_monitor.state_dir            -> a shared network path
REM      mo_ref_order_monitor.webex.queue_file     -> a shared network path
REM    so they share one history + one alert queue.
REM ============================================================
setlocal
set "BAT=%~dp0run_mo_ref_order_monitor_portable.bat"
set "TN=MO_RefOrder_Monitor"

echo Creating 30-min job (09:15-17:00) on this laptop...
echo   -^> %BAT%
echo.

schtasks /Create /TN "%TN%" /TR "%BAT%" /SC DAILY /ST 09:15 /RI 30 /DU 0007:45 /F

echo.
schtasks /Query /TN "%TN%" /FO LIST | findstr /I "TaskName Next Status"
echo.
echo Done. Runs every 30 min from 09:15 to 17:00 (while this user is logged in).
echo Remove later with:  schtasks /Delete /TN "%TN%" /F
pause
endlocal
