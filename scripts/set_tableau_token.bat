@echo off
REM One-shot: sync latest code, then prompt for the NEW Tableau PAT name+secret
REM and write them into config.yaml (hidden input, clears read-only, no BOM).
REM Mint the token first in Tableau, then run this, then run_probe.bat.
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
echo Syncing latest code...
"%PY%" scripts\sync_from_github.py
echo.
"%PY%" scripts\set_tableau_token.py
echo.
pause
endlocal
