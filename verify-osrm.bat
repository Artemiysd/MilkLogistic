@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Checking self-hosted OSRM and comparing route with matrix...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\osrm\verify.ps1" -Full
echo.
pause
