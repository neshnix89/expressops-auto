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

echo [first run / py not present - using PowerShell fallback]
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "$wc=New-Object System.Net.WebClient;" ^
  "$wc.Proxy=[System.Net.WebRequest]::GetSystemWebProxy();" ^
  "$wc.Proxy.Credentials=[System.Net.CredentialCache]::DefaultNetworkCredentials;" ^
  "$tmp=Join-Path $env:TEMP ('eo_syncdev_'+[guid]::NewGuid().ToString('N'));" ^
  "New-Item -ItemType Directory -Path $tmp | Out-Null;" ^
  "$zip=Join-Path $tmp 'branch.zip';" ^
  "Write-Host '[1/3] downloading...';" ^
  "$wc.DownloadFile('%ZIPURL%',$zip);" ^
  "Write-Host '[2/3] extracting...';" ^
  "Expand-Archive -Path $zip -DestinationPath $tmp -Force;" ^
  "$src=(Get-ChildItem -Path $tmp -Directory | Select-Object -First 1).FullName;" ^
  "Write-Host '[3/3] copying over dev dir (no deletes)...';" ^
  "New-Item -ItemType Directory -Path '%DEV%' -Force | Out-Null;" ^
  "Copy-Item -Path (Join-Path $src '*') -Destination '%DEV%' -Recurse -Force;" ^
  "Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Write-Host 'SYNC DEV OK - config.yaml and other local-only files left untouched.'"

:done
if errorlevel 1 (
    echo.
    echo [ERROR] Dev sync failed - check network / proxy and try again.
)
echo.
pause
endlocal
