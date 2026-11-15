@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Checking the whole stack...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\preflight.ps1"
echo.
pause
