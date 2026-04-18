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
echo [SYNC] Downloading latest code from GitHub...
cd /d "%PROJECT_DIR%"

:: Backup config.yaml and logs before sync (preserve secrets and history)
if exist "config\config.yaml" copy /y "config\config.yaml" "config\config.yaml.bak" >nul 2>&1
if exist "logs" xcopy /e /i /y "logs" "logs_bak" >nul 2>&1

:: Download repo as zip from GitHub using PowerShell (no git needed)
powershell -Command "try { Invoke-WebRequest -Uri 'https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip' -OutFile '%TEMP%\expressops-auto.zip' -UseBasicParsing } catch { Write-Host '[ERROR] Download failed:' $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo [ERROR] Failed to download from GitHub. Check your network connection.
    exit /b 1
)

:: Extract zip (overwrites existing files)
powershell -Command "try { Expand-Archive -Path '%TEMP%\expressops-auto.zip' -DestinationPath '%TEMP%\expressops-auto-extract' -Force } catch { Write-Host '[ERROR] Extract failed:' $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo [ERROR] Failed to extract zip.
    exit /b 1
)

:: Copy extracted files over current directory (skip config.yaml)
xcopy /e /y "%TEMP%\expressops-auto-extract\expressops-auto-main\*" "%PROJECT_DIR%" /exclude:%PROJECT_DIR%sync_exclude.txt >nul 2>&1
if not exist "%PROJECT_DIR%sync_exclude.txt" (
    :: If exclude file doesn't exist, just copy everything
    xcopy /e /y "%TEMP%\expressops-auto-extract\expressops-auto-main\*" "%PROJECT_DIR%" >nul 2>&1
)

:: Restore config.yaml and logs
if exist "config\config.yaml.bak" (
    copy /y "config\config.yaml.bak" "config\config.yaml" >nul 2>&1
    del "config\config.yaml.bak" >nul 2>&1
)
if exist "logs_bak" (
    xcopy /e /i /y "logs_bak" "logs" >nul 2>&1
    rmdir /s /q "logs_bak" >nul 2>&1
)

:: Clean up temp files
rmdir /s /q "%TEMP%\expressops-auto-extract" >nul 2>&1
del "%TEMP%\expressops-auto.zip" >nul 2>&1

echo [SYNC] Installing/updating dependencies...
"%PYTHON%" -m pip install -r requirements.txt --quiet
echo [SYNC] Done. Code updated from GitHub.
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
