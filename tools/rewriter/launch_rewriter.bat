@echo off
rem Open Rewrite Desk as a small always-on-top window. Run once a day.
rem Add --no-top to keep it as a normal window.
rem
rem Python is resolved the same way scripts\sync_now.bat does, because the
rem company laptop does not always have python on PATH.
setlocal
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0launch_rewriter.pyw" %*
    goto :eof
)
where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0launch_rewriter.pyw" %*
    goto :eof
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "%~dp0launch_rewriter.pyw" %*
    goto :eof
)
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0launch_rewriter.pyw" %*
    goto :fail
)
python "%~dp0launch_rewriter.pyw" %*
:fail
if errorlevel 1 (
    echo.
    echo [ERROR] Could not start Rewrite Desk. See tools\rewriter\rewriter.log
    pause
)
endlocal
