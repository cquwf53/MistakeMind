@echo off
title 考研错题归因系统
cd /d "E:\myprojects\test"
echo 正在启动错题归因系统...
echo 浏览器打开 http://127.0.0.1:8000
start http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
