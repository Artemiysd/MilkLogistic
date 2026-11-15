@echo off
chcp 65001 >nul
curl.exe -s -o nul http://localhost:8000/api/health
if errorlevel 1 (
  echo Backend is not running. Start it first: start.bat
  pause
  exit /b 1
)
start "" http://localhost:8000/
