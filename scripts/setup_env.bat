@echo off
echo ============================================
echo  ExpressOPS Automation - First Time Setup
echo ============================================
echo.

set "PYTHON=C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"

:: Check Python exists
if not exist "%PYTHON%" (
    echo [ERROR] Python not found at: %PYTHON%
    echo Please update the PYTHON path in this script.
    pause
    exit /b 1
)

echo [1/4] Python found: %PYTHON%

:: Check pip
echo [2/4] Checking pip...
"%PYTHON%" -m pip --version
if errorlevel 1 (
    echo [ERROR] pip not available. Please install pip.
    pause
    exit /b 1
)

:: Install dependencies
echo [3/4] Installing dependencies...
"%PYTHON%" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [WARN] Some dependencies may have failed to install.
    echo        pyodbc and oracledb may need to be installed separately.
)

:: Check config
echo [4/4] Checking configuration...
if not exist "config\config.yaml" (
    echo.
    echo [ACTION REQUIRED] No config.yaml found.
    echo   1. Copy config\config.example.yaml to config\config.yaml
    echo   2. Fill in your PAT tokens and connection strings
    echo.
    copy "config\config.example.yaml" "config\config.yaml"
    echo   config.yaml created from template — edit it now.
) else (
    echo   config.yaml found.
)

echo.
echo Setup complete! Next steps:
echo   1. Edit config\config.yaml with your credentials
echo   2. Run: ops list     (to see available tasks)
echo   3. Run: ops sync     (to pull latest code)
echo.
pause
