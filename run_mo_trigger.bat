@echo off
cd /d C:\Users\tmoghanan\Documents\AI\expressops-auto
set PYTHONIOENCODING=utf-8
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe -m tasks.mo_trigger_comment.main run --live --publish >> logs\mo_trigger.log 2>&1
