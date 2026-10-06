@echo off
cd /d "%~dp0"
:start
".venv\Scripts\python.exe" -m monitor.main --loop
echo Monitor dung bat thuong, khoi dong lai sau 30 giay...
timeout /t 30 /nobreak >nul
goto start
