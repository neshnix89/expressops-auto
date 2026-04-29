@echo off
REM bom_scanner daily scan — runs at 9:30 AM via Task Scheduler
REM Publishes results to Confluence only (no JIRA comments)

set PYTHON="C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe"
set ROOT=C:\Users\tmoghanan\Documents\AI\expressops-auto

cd /d %ROOT%
%PYTHON% -m tasks.bom_scanner.main scan --live --target-status 310
