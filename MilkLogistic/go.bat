@echo off
cd /d "%~dp0"
title MilkLogistic - start and check

echo.
echo === 1/4 Build and start containers (rebuild may take a couple of minutes) ===
docker compose -f docker-compose.yml -f docker-compose.backend.yml up -d --build
if errorlevel 1 goto fail

echo.
echo === 2/4 Waiting for API (up to 90 seconds) ===
set tries=0
:wait
timeout /t 3 /nobreak >nul
set /a tries=tries+1
curl -s -o nul -m 3 http://localhost:8000/api/health
if not errorlevel 1 goto api_ok
if %tries% GEQ 30 goto fail_api
goto wait
:api_ok
echo API is up.

echo.
echo === 3/4 Plan verification ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_plan.ps1"
if errorlevel 1 goto fail_check

echo.
echo === 4/4 Opening the web interface ===
start http://localhost:8080
start "Backend report" http://localhost:8000
echo.
echo Done. To stop everything: stop.bat
pause
exit /b 0

:fail
echo.
echo ERROR: docker compose failed to build or start the stack.
echo Most often Docker Desktop is off - start it and run go.bat again.
pause
exit /b 1

:fail_api
echo.
echo ERROR: API did not answer within 90 seconds. Logs: docker compose logs api
pause
exit /b 1

:fail_check
echo.
echo ERROR: plan verification failed. Logs: docker compose logs api
pause
exit /b 1
