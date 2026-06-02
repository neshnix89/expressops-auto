@echo off
REM ============================================================
REM  One-click DISCOVERY PROBE runner (company laptop).
REM  Double-click this. It:
REM    1. syncs the latest probe from GitHub (no git needed)
REM    2. runs it against live systems with writes HARD-BLOCKED
REM    3. opens the output for you
REM  Read-only by construction — see scripts\readonly_guard.py.
REM ============================================================
setlocal
set "PY=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto

echo [1/3] Syncing latest probe from GitHub...
"%PY%" scripts\sync_from_github.py
if errorlevel 1 (
    echo [ERROR] sync failed — check network. Running existing local probe.
)

echo [2/3] Running probe (READ-ONLY guard active)...
"%PY%" -m scripts.run_probe

echo [3/3] Opening output...
start "" notepad "outputs\_probe_latest.txt"
endlocal
