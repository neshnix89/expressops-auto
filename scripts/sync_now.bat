@echo off
REM ============================================================
REM  SYNC NOW - manual one-click "pull latest from GitHub".
REM  Primary path uses the proven scripts\sync_from_github.py
REM  (Python urllib picks up the corporate proxy automatically).
REM  Falls back to PowerShell (TLS1.2 + system proxy) only if that
REM  script isn't present yet.
REM
REM  Copy this ONE file anywhere (e.g. Desktop) and double-click
REM  any time you want the latest code. Overwrites repo files,
REM  never deletes - config.yaml / EDMAdmin.exe / outputs are kept.
REM
REM  Install dir is resolved in this order, so the SAME file works on
REM  a second laptop with a different Windows user:
REM    1. path passed as an argument  ->  sync_now.bat D:\work\expressops-auto
REM    2. the folder this file sits in, if it looks like the repo
REM       (used when run from inside scripts\ or the repo root)
REM    3. %USERPROFILE%\Documents\AI\expressops-auto  (per-user default)
REM ============================================================
setlocal
set "ZIPURL=https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip"

REM --- 1. explicit path argument wins
set "INSTALL=%~1"

REM --- 2. auto-detect: this file's folder, or its parent (scripts\)
if not defined INSTALL (
    if exist "%~dp0CLAUDE.md" set "INSTALL=%~dp0"
)
if not defined INSTALL (
    for %%I in ("%~dp0..") do set "PARENT=%%~fI"
    call :checkparent
)

REM --- 3. per-user default (works for any Windows account)
if not defined INSTALL set "INSTALL=%USERPROFILE%\Documents\AI\expressops-auto"

REM strip any trailing backslash so paths concatenate cleanly
if "%INSTALL:~-1%"=="\" set "INSTALL=%INSTALL:~0,-1%"

REM --- Python: explicit install, else the py launcher / PATH
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
    where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
)
goto begin

:checkparent
if exist "%PARENT%\CLAUDE.md" set "INSTALL=%PARENT%"
goto :eof

:begin

echo Pulling latest from GitHub into:
echo    %INSTALL%
echo.

if exist "%INSTALL%\scripts\sync_from_github.py" (
    echo [via Python sync_from_github.py]
    cd /d "%INSTALL%"
    REM unquoted: %PY% may be "py -3" (launcher + arg), not a single path
    %PY% scripts\sync_from_github.py
    goto done
)

echo [sync_from_github.py not found - using PowerShell fallback]
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "$wc=New-Object System.Net.WebClient;" ^
  "$wc.Proxy=[System.Net.WebRequest]::GetSystemWebProxy();" ^
  "$wc.Proxy.Credentials=[System.Net.CredentialCache]::DefaultNetworkCredentials;" ^
  "$tmp=Join-Path $env:TEMP ('eo_sync_'+[guid]::NewGuid().ToString('N'));" ^
  "New-Item -ItemType Directory -Path $tmp | Out-Null;" ^
  "$zip=Join-Path $tmp 'main.zip';" ^
  "Write-Host '[1/3] downloading...';" ^
  "$wc.DownloadFile('%ZIPURL%',$zip);" ^
  "Write-Host '[2/3] extracting...';" ^
  "Expand-Archive -Path $zip -DestinationPath $tmp -Force;" ^
  "$src=Join-Path $tmp 'expressops-auto-main';" ^
  "if(-not (Test-Path $src)){ $src=(Get-ChildItem -Path $tmp -Directory | Select-Object -First 1).FullName };" ^
  "Write-Host '[3/3] copying over install dir (no deletes)...';" ^
  "New-Item -ItemType Directory -Path '%INSTALL%' -Force | Out-Null;" ^
  "Copy-Item -Path (Join-Path $src '*') -Destination '%INSTALL%' -Recurse -Force;" ^
  "Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Write-Host 'SYNC OK - config.yaml and other local-only files left untouched.'"

:done
if errorlevel 1 (
    echo.
    echo [ERROR] Sync failed - check network / proxy and try again.
)
echo.
pause
endlocal
