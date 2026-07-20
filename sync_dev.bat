@echo off
REM ============================================================
REM  SYNC DEV - one-click "pull latest FEATURE BRANCH from GitHub"
REM  into a SEPARATE folder (expressops-auto-dev), so the
REM  production `main` checkout (expressops-auto) is never touched.
REM
REM  Primary path uses expressops-auto-dev\scripts\sync_dev_from_github.py
REM  once the dev folder exists; on the FIRST run (empty dev folder)
REM  it falls back to the self-contained PowerShell downloader below.
REM
REM  Copy this ONE file anywhere (e.g. Desktop) and double-click to
REM  refresh the dev checkout. Overwrites repo files, never deletes -
REM  config.yaml / outputs are kept.
REM
REM  To target a different feature branch, change BRANCH below (and,
REM  if you use the Python primary path, BRANCH in sync_dev_from_github.py).
REM ============================================================
setlocal
set "DEV=C:\Users\tmoghanan\Documents\AI\expressops-auto-dev"
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "BRANCH=claude/m3-ref-order-jira-monitor-4vmkre"
set "ZIPURL=https://github.com/neshnix89/expressops-auto/archive/refs/heads/%BRANCH%.zip"

echo Pulling FEATURE BRANCH into:
echo    %DEV%
echo    branch: %BRANCH%
echo.

if exist "%DEV%\scripts\sync_dev_from_github.py" (
    echo [via Python sync_dev_from_github.py]
    "%PY%" "%DEV%\scripts\sync_dev_from_github.py"
    goto done
)

echo [first run - download via Python urllib (proxy-aware), unzip via PowerShell]
set "TMPD=%TEMP%\eo_syncdev"
if exist "%TMPD%" rmdir /s /q "%TMPD%"
mkdir "%TMPD%"
echo [1/3] downloading branch zip...
"%PY%" -c "import urllib.request; urllib.request.urlretrieve('%ZIPURL%', r'%TMPD%\branch.zip')"
if errorlevel 1 goto fail
echo [2/3] extracting + [3/3] copying over dev dir (no deletes)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "Expand-Archive -Path '%TMPD%\branch.zip' -DestinationPath '%TMPD%' -Force;" ^
  "$src=(Get-ChildItem -Path '%TMPD%' -Directory | Select-Object -First 1).FullName;" ^
  "New-Item -ItemType Directory -Path '%DEV%' -Force | Out-Null;" ^
  "Copy-Item -Path (Join-Path $src '*') -Destination '%DEV%' -Recurse -Force;" ^
  "Write-Host 'SYNC DEV OK - config.yaml and other local-only files left untouched.'"
if errorlevel 1 goto fail
rmdir /s /q "%TMPD%"
goto done

:fail
echo.
echo [ERROR] Dev sync failed - check network / proxy and try again.
goto end

:done
echo.
echo Done.

:end
echo.
pause
endlocal
