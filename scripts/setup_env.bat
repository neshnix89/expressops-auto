@echo off
echo ============================================
echo  ExpressOPS Automation - First Time Setup
echo ============================================
echo.

REM Resolve Python the way every other runner does. This used to hardcode one
REM person's profile path, which made the script a no-op on any other machine —
REM the same class of bug already fixed in the MR and KPI runners.
set "PYTHON=%EXPRESSOPS_PY%"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON if exist "%ProgramFiles%\Python312\python.exe" set "PYTHON=%ProgramFiles%\Python312\python.exe"
REM `where python` also matches the Microsoft Store alias stub under
REM %LOCALAPPDATA%\Microsoft\WindowsApps, which is not Python and fails.
if not defined PYTHON for /f "delims=" %%i in ('where python 2^>nul ^| findstr /i /v /c:"\WindowsApps"') do if not defined PYTHON set "PYTHON=%%i"

if not defined PYTHON (
    echo [ERROR] python.exe not found. Install Python 3.12, or set EXPRESSOPS_PY.
    pause
    exit /b 1
)

REM Run from the repo root regardless of where this was launched from, so
REM requirements.txt and config\ resolve.
cd /d "%~dp0.."

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
