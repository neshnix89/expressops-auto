@echo off
REM ============================================================
REM  DOUBLE-CLICK ME before asking IT for anything.
REM
REM  Tests everything this machine needs — Python packages, the
REM  JIRA token (and WHOSE it is), the M3 ODBC login plus each
REM  table, read AND write on the shared state folder, and the
REM  Webex desktop app.
REM
REM  Writes logs\access_check.txt and opens it in Notepad. Any
REM  failures are collected into a single ready-to-paste IT
REM  request at the bottom, so one ticket covers everything
REM  instead of discovering the next gap tomorrow.
REM
REM  Read-only apart from one temp file on the shared folder,
REM  which is itself one of the things being tested. Secrets are
REM  never printed - safe to paste.
REM ============================================================
setlocal
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py"

if not exist logs mkdir logs
set "OUT=logs\access_check.txt"
set PYTHONIOENCODING=utf-8

echo Checking access... this takes about a minute.
echo.

"%PY%" scripts\check_access.py > "%OUT%" 2>&1

echo Done. Opening %OUT%
start notepad "%OUT%"
endlocal
