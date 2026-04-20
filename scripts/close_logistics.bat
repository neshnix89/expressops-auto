@echo off
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
echo Closing Logistics WPs where TO status ^>= 90...
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe -m tasks.to_status_check.main --live --close-ready
echo.
pause
