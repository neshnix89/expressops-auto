@echo off
REM ops.bat — ExpressOPS automation CLI
REM Usage: ops push "commit message"
REM        ops sync
REM        ops run <task>
REM        ops test <task>

set PYTHON="C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set ROOT=C:\Users\tmoghanan\Documents\AI\expressops-auto

if "%1"=="push" (
    set MSG=%~2
    if "%MSG%"=="" set MSG=sync from company laptop
    %PYTHON% "%ROOT%\ops_push.py" %MSG%
    goto :eof
)

if "%1"=="sync" (
    echo Syncing from GitHub...
    curl.exe -L -o "%TEMP%\expressops.zip" "https://github.com/neshnix89/expressops-auto/archive/refs/heads/main.zip"
    powershell -ExecutionPolicy Bypass -Command ^
        "Expand-Archive -Path '%TEMP%\expressops.zip' -DestinationPath '%TEMP%\expressops' -Force; Copy-Item -Path '%TEMP%\expressops\expressops-auto-main\*' -Destination '%ROOT%' -Recurse -Force"
    echo Done.
    goto :eof
)

if "%1"=="run" (
    %PYTHON% "%ROOT%\tasks\%2\main.py"
    goto :eof
)

if "%1"=="test" (
    %PYTHON% "%ROOT%\tasks\%2\main.py" --mock
    goto :eof
)

echo Usage:
echo   ops push "commit message"  — Push files to GitHub
echo   ops sync                   — Pull latest from GitHub
echo   ops run ^<task^>             — Run a task
echo   ops test ^<task^>            — Run a task in mock mode
