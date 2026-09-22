@echo off
REM ============================================================
REM  SMT BUILD GATE IMPACT (company laptop) — read-only.
REM  Double-click this AFTER sync_now.bat and BEFORE the 09:30
REM  scheduled overlay run, to see what the new SMT Build start
REM  gate does to the live board.
REM
REM  SMT Build used to start at max(Material, PCB). It now waits
REM  for Routing / PE / TE - TechnPrep as well, each Done,
REM  Acknowledged or Won't Do.
REM
REM  This reads live JIRA and compares the OLD gate against the
REM  NEW one for every open container. It writes nothing to JIRA
REM  or Confluence and does not touch the published cache.
REM
REM  Watch for "NOW GREY": a container whose tech-prep was left
REM  open in JIRA loses its SMT Build number until that package
REM  is closed. The report names the package for each one.
REM ============================================================
setlocal
REM Repo root = the folder this .bat lives in, never a hardcoded profile path.
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

set "PY=%EXPRESSOPS_PY%"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
REM `where python` also matches the Microsoft Store alias stub under
REM %LOCALAPPDATA%\Microsoft\WindowsApps. That stub is not Python: running it
REM only prints "Python was not found; run without arguments to install from
REM the Microsoft Store" and fails. Filter it out INSIDE the for, so the
REM fallback can never hand back a path that breaks every call after it.
if not defined PY for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i /v /c:"\WindowsApps"') do if not defined PY set "PY=%%i"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
set PYTHONIOENCODING=utf-8

if not defined PY (
    echo [ERROR] python.exe not found. Install Python 3.12, or set EXPRESSOPS_PY.
    pause
    exit /b 9
)

echo [1/3] Cleaning config.yaml...
"%PY%" scripts\clean_config.py

echo [2/3] Comparing the old SMT Build gate against the new one (live JIRA)...
"%PY%" scripts\smt_gate_impact.py --live
set "RC=%ERRORLEVEL%"

echo [3/3] Opening the report...
if exist "outputs\smt_gate_impact.txt" (
    start "" notepad "outputs\smt_gate_impact.txt"
) else (
    echo [WARN] no report file written - read the console output above.
)

echo.
echo Nothing was written to JIRA or Confluence.
endlocal & exit /b %RC%
