@echo off
REM ============================================================
REM  ONE-TIME: re-point the existing daily "\MR_Status_Report"
REM  task to the NEW module so it stops wiping the Status column.
REM  Uses schtasks /Change /TR, which swaps ONLY the program and
REM  keeps the existing 10:00 daily trigger + run-as account +
REM  saved credentials. Double-click once.
REM ============================================================
setlocal
echo Re-pointing \MR_Status_Report to the new module...
schtasks /Change /TN "\MR_Status_Report" /TR "C:\Users\tmoghanan\Documents\AI\expressops-auto\scheduled_mr_publish.bat"
echo.
echo Current definition:
schtasks /Query /TN "\MR_Status_Report" /V /FO LIST
echo.
echo Done. The daily 10:00 job now runs the new MR report (with the Status column).
pause
endlocal
