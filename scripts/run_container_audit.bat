@echo off
set PYTHONIOENCODING=utf-8
set PYTHONWARNINGS=ignore::urllib3.exceptions.InsecureRequestWarning
C:\Users\tmoghanan\AppData\Local\Programs\Python\Python312\python.exe tasks\container_template_audit\main.py %*