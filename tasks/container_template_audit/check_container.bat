@echo off
REM ============================================================
REM  Manual one-by-one CONTAINER AUDIT (company laptop).
REM
REM  Usage:  check_container.bat KEY [KEY2 KEY3 ...]
REM    e.g.  check_container.bat NPIOTHER-5124
REM          check_container.bat POSX-7007 OBXR100-690
REM
REM  Audits the given container(s) against ALL audit rules
REM  (including the WP-design checks) and prints the findings.
REM  Read-only: it never writes to JIRA or Confluence.
REM
REM  Unlike the batch scan, this audits not-yet-deployed
REM  containers too (manual mode), so you can check a container
REM  the moment it is requested.
REM
REM  Tip: run scripts\run_audit.bat first if you want the
REM  latest code synced from GitHub.
REM ============================================================
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONWARNINGS=ignore"
set "ROOT=C:\Users\tmoghanan\Documents\AI\expressops-auto"
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "OUT=%ROOT%\outputs\_manual_check.txt"

if "%~1"=="" (
    echo Usage: check_container.bat KEY [KEY2 ...]
    echo    e.g. check_container.bat NPIOTHER-5124
    pause
    exit /b 1
)

cd /d "%ROOT%"
echo Checking %* (live, read-only)...
"%PY%" tasks\container_template_audit\batch.py check --live %* > "%OUT%" 2>&1

echo.
echo ============================================================
echo  Done. Findings written to:
echo    %OUT%
echo ============================================================
notepad "%OUT%"
endlocal
