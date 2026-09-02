@echo off
rem Open Rewrite Desk as a small always-on-top window. Run once a day.
rem Add --no-top to keep it as a normal window.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "%~dp0launch_rewriter.pyw" %*
) else (
    start "" /min python "%~dp0launch_rewriter.pyw" %*
)
