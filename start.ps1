<#
  Запуск MilkLogistic одной командой.

    .\start.ps1              # поднять стек: web + api(заглушка) + routing + db
    .\start.ps1 -Build       # то же, но пересобрать образы (после правок кода)
    .\start.ps1 -Osrm        # дополнительно поднять свой OSRM (нужен prepare.ps1)
    .\stop.ps1               # остановить всё

  Запускать из PowerShell. Права администратора не нужны.
  Docker Desktop должен быть установлен; если он выключен — скрипт попробует запустить.
#>
param(
    [switch]$Build,
    [switch]$Osrm,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step([string]$text) { Write-Host "`n>> $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "   [OK] $text" -ForegroundColor Green }
function Fail([string]$text) { Write-Host "   [!!] $text" -ForegroundColor Red }

# ---------------------------------------------------------------- 1. docker

Step "Ищу Docker"
$dockerExe = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $dockerExe) {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )) {
        if (Test-Path $candidate) { $dockerExe = $candidate; break }
    }
}
if (-not $dockerExe) {
    Fail "Docker не найден."
    Write-Host "   Установи Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
}
$env:PATH = "$(Split-Path $dockerExe);$env:PATH"
Ok "docker: $(& $dockerExe --version)"

Step "Проверяю, запущен ли Docker Desktop"
& $dockerExe info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $desktop = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $desktop) { Fail "Docker Desktop выключен, а найти его не удалось. Запусти вручную."; exit 1 }

    Write-Host "   Docker Desktop выключен, запускаю (это до 1-2 минут)..." -ForegroundColor Yellow
    Start-Process $desktop
    $ready = $false
    for ($i = 0; $i -lt 50; $i++) {
        Start-Sleep -Seconds 3
        & $dockerExe info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Write-Host "." -NoNewline
    }
    if (-not $ready) { Fail "Docker Desktop не поднялся за 2.5 минуты. Открой его вручную и запусти скрипт снова."; exit 1 }
}
Ok "Docker работает"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Ok "создал .env из .env.example"
}

# ---------------------------------------------------------------- 2. compose

if ($Osrm) {
    if (-not (Test-Path "infra/osrm/data/grodno.osrm")) {
        Fail "Нет данных OSRM. Сначала: .\infra\osrm\prepare.ps1"
        exit 1
    }
    Step "Поднимаю стек вместе со своим OSRM"
    if ($Build) { & $dockerExe compose --profile geo up -d --build } else { & $dockerExe compose --profile geo up -d }
} else {
    Step "Поднимаю стек"
    if ($Build) { & $dockerExe compose up -d --build } else { & $dockerExe compose up -d }
}
if ($LASTEXITCODE -ne 0) { Fail "docker compose завершился с ошибкой. Смотри вывод выше."; exit 1 }

# ---------------------------------------------------------------- 3. ожидание

Step "Жду, пока сервисы ответят"
$api = $false
$routing = $false
for ($i = 0; $i -lt 30; $i++) {
    if (-not $api) {
        try { Invoke-RestMethod "http://localhost:8000/api/health" -TimeoutSec 3 | Out-Null; $api = $true } catch { }
    }
    if (-not $routing) {
        try { Invoke-RestMethod "http://localhost:8100/api/health" -TimeoutSec 5 | Out-Null; $routing = $true } catch { }
    }
    if ($api -and $routing) { break }
    Start-Sleep -Seconds 2
    Write-Host "." -NoNewline
}

if ($api) { Ok "API отвечает" } else { Fail "API не отвечает: docker compose logs api" }
if ($routing) { Ok "сервис маршрутизации отвечает" } else { Fail "routing не отвечает: docker compose logs routing" }

# ---------------------------------------------------------------- 4. итог

Step "Готово"
Write-Host ""
Write-Host "   Интерфейс .............. http://localhost:8080"
Write-Host "   API .................... http://localhost:8000/api/health"
Write-Host "   Маршрутизация .......... http://localhost:8100/api/health"
Write-Host "   Swagger маршрутизации .. http://localhost:8100/docs"
Write-Host ""

try {
    $health = Invoke-RestMethod "http://localhost:8100/api/health" -TimeoutSec 15
    if ($health.osrm.reachable) {
        Ok "OSRM доступен: $($health.osrm.base_url), $($health.osrm.latency_ms) мс"
    } else {
        Write-Host "   [!!] OSRM недоступен. Если нужен офлайн-режим:" -ForegroundColor Yellow
        Write-Host "        .\infra\osrm\prepare.ps1" -ForegroundColor Yellow
        Write-Host "        .\start.ps1 -Osrm" -ForegroundColor Yellow
    }
} catch {
    Write-Host "   [!!] не смог проверить OSRM" -ForegroundColor Yellow
}

Write-Host "   Проверка целиком: .\infra\preflight.ps1" -ForegroundColor DarkGray
Write-Host "   Остановить:       .\stop.ps1" -ForegroundColor DarkGray
if (-not $NoBrowser) { Start-Process "http://localhost:8080" }
