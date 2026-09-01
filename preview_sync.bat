@echo off
REM ============================================================
REM  PREVIEW SYNC — what sync_now.bat WOULD change, without changing it.
REM  Double-click this BEFORE sync_now.bat when you want to know what
REM  is about to land.
REM
REM  It downloads the GitHub main zip to a temp folder, compares it to
REM  the files on this machine, and prints three lists:
REM     - which of your files would be OVERWRITTEN (the real blast radius)
REM     - which files are NEW (nothing of yours is replaced)
REM     - which files are LOCAL-ONLY and therefore kept (config.yaml, ...)
REM
REM  Nothing on this machine is modified. To see the actual line changes
REM  in one file:
REM     python scripts\sync_preview.py --diff core\m3.py
REM ============================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

REM Python: the standard per-user install, else the py launcher / PATH.
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
    where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
)

%PY% scripts\sync_preview.py --report

echo.
echo A copy of this report is in outputs\sync_preview.txt
endlocal
pause
