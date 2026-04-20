@echo off
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe -m tasks.to_status_check.main --live --publish >> logs\to_status_check_bat.log 2>&1
