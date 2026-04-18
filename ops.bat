@echo off
setlocal enabledelayedexpansion

:: ============================================================
:: ExpressOPS Automation Runner
:: Usage:  ops sync              - Pull latest code from Git
::         ops list              - List all available tasks
::         ops test <task>       - Run task in mock mode
::         ops run <task>        - Run task in LIVE mode
::         ops capture <task>    - Capture mock data from live systems
::         ops status            - Show last run status of all tasks
::         ops schedule <task>   - Show Task Scheduler command for this task
:: ============================================================

set "PYTHON=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set "PROJECT_DIR=%~dp0"
set "LOG_DIR=%PROJECT_DIR%logs"

:: Create logs directory if it doesn't exist
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Parse command
set "CMD=%~1"
set "TASK=%~2"

if "%CMD%"=="" goto :usage
if "%CMD%"=="sync" goto :sync
if "%CMD%"=="list" goto :list
if "%CMD%"=="test" goto :test
if "%CMD%"=="run" goto :run
if "%CMD%"=="capture" goto :capture
if "%CMD%"=="status" goto :status
if "%CMD%"=="schedule" goto :schedule
goto :usage

:: ----------------------------------------
:sync
echo [SYNC] Pulling latest code from Git...
cd /d "%PROJECT_DIR%"
git pull origin main
if errorlevel 1 (
    echo [ERROR] Git pull failed. Check your network connection.
    exit /b 1
)
echo [SYNC] Installing/updating dependencies...
"%PYTHON%" -m pip install -r requirements.txt --quiet
echo [SYNC] Done.
goto :eof

:: ----------------------------------------
:list
echo.
echo Available Tasks:
echo ================
for /d %%D in ("%PROJECT_DIR%tasks\*") do (
    set "taskname=%%~nxD"
    if exist "%%D\main.py" (
        echo   !taskname!
    )
)
echo.
goto :eof

:: ----------------------------------------
:test
if "%TASK%"=="" (
    echo [ERROR] Specify a task name. Usage: ops test ^<task^>
    exit /b 1
)
if not exist "%PROJECT_DIR%tasks\%TASK%\main.py" (
    echo [ERROR] Task '%TASK%' not found. Run 'ops list' to see available tasks.
    exit /b 1
)
echo [TEST] Running %TASK% in MOCK mode...
cd /d "%PROJECT_DIR%"
"%PYTHON%" -m tasks.%TASK%.main --mock
echo [TEST] %TASK% completed. Check logs\%TASK%.log for details.
goto :eof

:: ----------------------------------------
:run
if "%TASK%"=="" (
    echo [ERROR] Specify a task name. Usage: ops run ^<task^>
    exit /b 1
)
if not exist "%PROJECT_DIR%tasks\%TASK%\main.py" (
    echo [ERROR] Task '%TASK%' not found. Run 'ops list' to see available tasks.
    exit /b 1
)
echo.
echo  *** WARNING: LIVE MODE — This will connect to real systems ***
echo.
set /p "CONFIRM=Type YES to confirm: "
if /i not "%CONFIRM%"=="YES" (
    echo [CANCELLED] Aborted.
    exit /b 0
)
echo [LIVE] Running %TASK% against live systems...
cd /d "%PROJECT_DIR%"
"%PYTHON%" -m tasks.%TASK%.main --live
echo [LIVE] %TASK% completed. Check logs\%TASK%.log for details.
goto :eof

:: ----------------------------------------
:capture
if "%TASK%"=="" (
    echo [ERROR] Specify a task name. Usage: ops capture ^<task^>
    exit /b 1
)
echo [CAPTURE] Saving mock data for %TASK% from live systems...
cd /d "%PROJECT_DIR%"
"%PYTHON%" scripts\capture_mock_data.py --task %TASK%
echo [CAPTURE] Done. Mock data saved to tasks\%TASK%\mock_data\
goto :eof

:: ----------------------------------------
:status
echo.
echo Task Status Overview
echo ====================
cd /d "%PROJECT_DIR%"
"%PYTHON%" scripts\show_status.py
goto :eof

:: ----------------------------------------
:schedule
if "%TASK%"=="" (
    echo [ERROR] Specify a task name. Usage: ops schedule ^<task^>
    exit /b 1
)
echo.
echo To schedule %TASK% in Windows Task Scheduler, use:
echo.
echo   Program: %PYTHON%
echo   Arguments: -m tasks.%TASK%.main --live
echo   Start in: %PROJECT_DIR%
echo.
goto :eof

:: ----------------------------------------
:usage
echo.
echo ExpressOPS Automation Runner
echo ============================
echo.
echo Usage:
echo   ops sync              Pull latest code + update dependencies
echo   ops list              List all available tasks
echo   ops test ^<task^>       Run task in MOCK mode (safe, no live systems)
echo   ops run ^<task^>        Run task in LIVE mode (connects to real systems)
echo   ops capture ^<task^>    Capture mock data from live systems for VPS testing
echo   ops status            Show last run status of all tasks
echo   ops schedule ^<task^>   Show Task Scheduler command for a task
echo.
goto :eof
