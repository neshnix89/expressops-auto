@echo off
REM ============================================================
REM  One-click PE/TE REPORT EDM PROBE (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest code from GitHub (no git needed)
REM    2. reads the live MR page for the real QD-* / 906-* numbers
REM    3. asks EDM what release state each of them is in
REM    4. saves + opens the output
REM
REM  READ-ONLY: SELECT statements only, and a single GET of the
REM  Confluence page. Nothing is ever written.
REM
REM  NOTE: runs under PLAIN python, not EDMAdmin.exe. core.edm
REM  spawns EDMAdmin.exe as a subprocess itself. Calling
REM  EDMAdmin.exe directly dies with 0xC0000135 (missing DLL).
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "OUT=outputs\_probe_edm_reports.txt"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8

echo [1/4] Syncing latest code from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 echo [WARN] sync failed - running existing local copy.

echo [2/4] Cleaning config.yaml...
"%PY%" scripts\clean_config.py

echo [3/4] Probing EDM for PE/TE report release state (READ-ONLY)...
if not exist "outputs" mkdir "outputs"
"%PY%" -m scripts.probe_edm_reports > "%OUT%" 2>&1
if errorlevel 1 (
    echo [ERROR] probe failed - see %OUT%
)

echo [4/4] Opening output...
start "" notepad "%OUT%"
endlocal
