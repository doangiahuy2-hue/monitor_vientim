@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ====================================================
echo    KIỂM TRA BÀI VIẾT MỚI - VIỆN TIM TP.HCM (NGAY)
echo ====================================================
echo.
".venv\Scripts\python.exe" -m monitor.main --once --on-demand
echo.
echo ====================================================
echo Hoàn tất. Nhấn phím bất kỳ để đóng...
pause >nul
