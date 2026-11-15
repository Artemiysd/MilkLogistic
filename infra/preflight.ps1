<#
  Предполётная проверка окружения перед демо.
  Запускать в PowerShell из корня репозитория:
      .\infra\preflight.ps1
  Проверяет то, что реально ломается на защите: докер, порты, API, матрица, OSRM.
#>

$ErrorActionPreference = "Continue"
$script:ok = 0
$script:fail = 0

function Warn([string]$message) {
    Write-Host "  [WARN] $message" -ForegroundColor Yellow
}

function Check([string]$name, [scriptblock]$test) {
    try {
        $result = & $test
        if ($result -eq $true) {
            Write-Host "  [OK]   $name" -ForegroundColor Green
            $script:ok++
        } else {
            Write-Host "  [FAIL] $name -> $result" -ForegroundColor Red
            $script:fail++
        }
    } catch {
        Write-Host "  [FAIL] $name -> $($_.Exception.Message)" -ForegroundColor Red
        $script:fail++
    }
}

Write-Host "`n1. Docker" -ForegroundColor Cyan
# Docker Desktop может быть не в PATH — ищем по типовым местам установки
$dockerExe = $null
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) {
    $dockerExe = $dockerCommand.Source
} else {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $dockerExe = $candidate
            $env:PATH = "$(Split-Path $candidate);$env:PATH"
            break
        }
    }
}
Check "docker CLI установлен" { if ($dockerExe) { $true } else { "не найден ни в PATH, ни в типовых местах установки" } }
Check "docker daemon запущен" {
    if (-not $dockerExe) { "нет docker" }
    else { & $dockerExe info --format "{{.ServerVersion}}" 2>$null | Out-Null; $LASTEXITCODE -eq 0 }
}

Write-Host "`n2. Порты" -ForegroundColor Cyan
foreach ($port in 8000, 8080, 8100, 5432) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Warn "порт $port занят (PID $($conn[0].OwningProcess)) — нормально, если это наш docker-стек"
    } else {
        Write-Host "  [OK]   порт $port свободен" -ForegroundColor Green
    }
}

Write-Host "`n3. Данные и матрица" -ForegroundColor Cyan
Check "data/logistics_data.json на месте" { Test-Path "data/logistics_data.json" }
Check "data/matrix_cache.json на месте" { Test-Path "data/matrix_cache.json" }

if ((Test-Path "data/logistics_data.json") -and (Test-Path "data/matrix_cache.json")) {
    $data = Get-Content "data/logistics_data.json" -Raw | ConvertFrom-Json
    $matrix = Get-Content "data/matrix_cache.json" -Raw | ConvertFrom-Json
    $expected = 1 + @($data.delivery_points).Count
    Check "размер матрицы совпадает с числом точек ($expected)" {
        if ($matrix.size -eq $expected) { $true } else { "в файле $($matrix.size)" }
    }
    Check "сумма груза 63 630 кг" {
        $sum = (@($data.delivery_points) | Measure-Object -Property demand -Sum).Sum
        if ($sum -eq 63630) { $true } else { "получилось $sum" }
    }
}

Write-Host "`n4. API" -ForegroundColor Cyan
Check "GET /api/health" {
    $response = Invoke-RestMethod "http://localhost:8000/api/health" -TimeoutSec 5
    if ($response.status -eq "ok") { $true } else { $response.status }
}
Check "GET /api/data через nginx (8080)" {
    $response = Invoke-RestMethod "http://localhost:8080/api/data" -TimeoutSec 10
    if (@($response.delivery_points).Count -gt 0) { $true } else { "пусто" }
}

Write-Host "`n5. Сервис маршрутизации" -ForegroundColor Cyan
Check "GET /api/health (8100)" {
    $response = Invoke-RestMethod "http://localhost:8100/api/health" -TimeoutSec 25
    if ($response.osrm.reachable) { $true } else { "OSRM недоступен: $($response.osrm.error.message)" }
}
Check "POST /api/calculate-route (3 точки)" {
    $body = @{
        points = @(
            @{ id = "depot";  lat = 53.708928;  lon = 23.828288 },
            @{ id = "point1"; lat = 53.6753356; lon = 23.9116412 },
            @{ id = "point2"; lat = 53.8446836; lon = 24.0 }
        )
    } | ConvertTo-Json -Depth 5
    $response = Invoke-RestMethod "http://localhost:8100/api/calculate-route" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 60
    if ($response.distance_m -gt 0) { $true } else { "distance_m=0" }
}

Write-Host "`n6. OSRM" -ForegroundColor Cyan
Check "публичный OSRM отвечает" {
    $url = "https://router.project-osrm.org/route/v1/driving/23.828,53.708;23.911,53.675?overview=false"
    $response = Invoke-RestMethod $url -TimeoutSec 10
    if ($response.code -eq "Ok") { $true } else { $response.code }
}
$ownOsrm = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
if ($ownOsrm) {
    Check "свой OSRM на порту 5000 отвечает" {
        $response = Invoke-RestMethod "http://localhost:5000/route/v1/driving/23.828,53.708;23.911,53.675?overview=false" -TimeoutSec 10
        if ($response.code -eq "Ok") { $true } else { $response.code }
    }
} else {
    Warn "свой OSRM не запущен — работаем через публичный (docker compose --profile geo up -d, чтобы поднять свой)"
}

Write-Host "`nИтог: $($script:ok) ок, $($script:fail) проблем" -ForegroundColor $(if ($script:fail -eq 0) { "Green" } else { "Yellow" })
if ($script:fail -gt 0) {
    Write-Host "Порты и Docker лечатся так: docker compose down, затем снова up." -ForegroundColor DarkGray
    exit 1
}
