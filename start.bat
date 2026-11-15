@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting MilkLogistic...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
echo.
echo Done. You can close this window.
pause
