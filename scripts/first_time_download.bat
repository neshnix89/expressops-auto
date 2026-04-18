@echo off
:: ============================================
:: ExpressOPS - First Time Download
:: Run this ONCE on company laptop to get the project.
:: After this, use "ops sync" to update.
:: ============================================

set "INSTALL_DIR=C:\Users\tmoghanan\Documents\expressops-auto"

echo.
echo ExpressOPS Automation - First Time Setup
echo =========================================
echo.
echo This will download the project to:
echo   %INSTALL_DIR%
echo.

if exist "%INSTALL_DIR%\CLAUDE.md" (
    echo [WARNING] Project already exists at %INSTALL_DIR%
    echo           Use "ops sync" to update instead.
    pause
    exit /b 0
)

echo [1/3] Downloading from GitHub...
powershell -Command "Invoke-WebRequest -Uri 'https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip' -OutFile '%TEMP%\expressops-auto.zip' -UseBasicParsing"
if errorlevel 1 (
    echo [ERROR] Download failed. Check your network.
    pause
    exit /b 1
)

echo [2/3] Extracting...
powershell -Command "Expand-Archive -Path '%TEMP%\expressops-auto.zip' -DestinationPath '%TEMP%\expressops-auto-extract' -Force"
if errorlevel 1 (
    echo [ERROR] Extract failed.
    pause
    exit /b 1
)

:: Move to final location
mkdir "%INSTALL_DIR%" 2>nul
xcopy /e /y "%TEMP%\expressops-auto-extract\expressops-auto-main\*" "%INSTALL_DIR%\" >nul

:: Clean up
rmdir /s /q "%TEMP%\expressops-auto-extract" >nul 2>&1
del "%TEMP%\expressops-auto.zip" >nul 2>&1

echo [3/3] Running setup...
cd /d "%INSTALL_DIR%"
call scripts\setup_env.bat

echo.
echo ============================================
echo  DONE! Project is at: %INSTALL_DIR%
echo.
echo  Next steps:
echo    1. Edit config\config.yaml with your PAT tokens
echo    2. cd %INSTALL_DIR%
echo    3. ops list
echo ============================================
echo.
pause
