@echo off
REM ============================================================
REM  Register (or re-point) the daily "MR_Status_Report" task.
REM
REM  Usage — double-click for the default 10:00, or from a prompt:
REM      setup_mr_schedule.bat 10:10
REM
REM  PORTABLE: the task is pointed at scheduled_mr_publish.bat in
REM  THIS folder, so each person's task runs their own checkout.
REM
REM  BACKUP RUNNER: a colleague running this as failover should
REM  pick a time 10+ minutes after the primary (e.g. 10:10) so the
REM  two runs never overlap on the same Confluence page. Both
REM  publish to the same page by design — the later run reads what
REM  the earlier one wrote and refreshes it.
REM ============================================================
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "WHEN=%~1"
if not defined WHEN set "WHEN=10:00"

echo Registering daily task "MR_Status_Report" at %WHEN%
echo   runner: %ROOT%\scheduled_mr_publish.bat
echo.
schtasks /Create /TN "MR_Status_Report" /TR "\"%ROOT%\scheduled_mr_publish.bat\"" /SC DAILY /ST %WHEN% /F
if errorlevel 1 (
    echo.
    echo [ERROR] Could not register the task.
    echo   - check the time format is HH:MM ^(24-hour^)
    echo   - some machines need this run as Administrator
    pause
    exit /b 1
)

echo.
echo Current definition:
schtasks /Query /TN "MR_Status_Report" /V /FO LIST
echo.
echo Done. Output goes to %ROOT%\logs\mr_scheduled.log
pause
endlocal
