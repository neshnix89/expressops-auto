@echo off
REM ============================================================
REM  SYNC NOW - manual one-click "pull latest from GitHub".
REM  Self-contained: downloads + extracts main.zip itself, so it
REM  works even if no repo file is present yet. Copy this ONE file
REM  anywhere on the laptop (e.g. Desktop) and double-click it any
REM  time you want the latest code.
REM
REM  Overwrites repo files in the install dir. Does NOT delete
REM  anything, so config.yaml / EDMAdmin.exe / outputs are kept.
REM ============================================================
setlocal
set "INSTALL=C:\Users\tmoghanan\Documents\AI\expressops-auto"
set "ZIPURL=https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip"

echo Pulling latest from GitHub into:
echo    %INSTALL%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$tmp=Join-Path $env:TEMP ('eo_sync_'+[guid]::NewGuid().ToString('N'));" ^
  "New-Item -ItemType Directory -Path $tmp | Out-Null;" ^
  "$zip=Join-Path $tmp 'main.zip';" ^
  "Write-Host '[1/3] downloading...';" ^
  "Invoke-WebRequest -Uri '%ZIPURL%' -OutFile $zip -UseBasicParsing;" ^
  "Write-Host '[2/3] extracting...';" ^
  "Expand-Archive -Path $zip -DestinationPath $tmp -Force;" ^
  "$src=Join-Path $tmp 'expressops-auto-main';" ^
  "if(-not (Test-Path $src)){ $src=(Get-ChildItem -Path $tmp -Directory | Select-Object -First 1).FullName };" ^
  "Write-Host '[3/3] copying over install dir (no deletes)...';" ^
  "New-Item -ItemType Directory -Path '%INSTALL%' -Force | Out-Null;" ^
  "Copy-Item -Path (Join-Path $src '*') -Destination '%INSTALL%' -Recurse -Force;" ^
  "Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue;" ^
  "Write-Host 'SYNC OK - config.yaml and other local-only files left untouched.'"

if errorlevel 1 (
    echo.
    echo [ERROR] Sync failed - check network / proxy and try again.
)
echo.
pause
endlocal
