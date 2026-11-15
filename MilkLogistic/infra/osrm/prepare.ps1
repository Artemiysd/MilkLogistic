<#
  Подготовка данных OSRM для Гродно (нужно один раз).
  Результат: infra/osrm/data/grodno.osrm* — их читает сервис osrm из docker-compose.

  Использование:
    .\infra\osrm\prepare.ps1            # скачает Беларусь и нарежет область Гродно
    .\infra\osrm\prepare.ps1 -SkipDownload

  Требуется Docker Desktop. Область нарезки — bbox вокруг Гродно (~40 км).
#>
param(
    [switch]$SkipDownload,
    [string]$Bbox = "23.60,53.55,24.10,53.90",
    [string]$Image = "osrm/osrm-backend:v5.25.0",
    [string]$OsmiumImage = "stefda/osmium-tool"
)

$ErrorActionPreference = "Stop"

# docker может быть не в PATH: ищем CLI в типовых местах установки
$dockerExe = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $dockerExe) {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )) {
        if (Test-Path $candidate) { $dockerExe = $candidate; break }
    }
}
if (-not $dockerExe) { throw "docker не найден. Установите Docker Desktop или добавьте его в PATH" }
$env:PATH = "$(Split-Path $dockerExe);$env:PATH"

$dataDir = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$pbf = Join-Path $dataDir "belarus-latest.osm.pbf"
$grodno = Join-Path $dataDir "grodno.osm.pbf"
$abs = (Resolve-Path $dataDir).Path

if (-not $SkipDownload -and -not (Test-Path $pbf)) {
    Write-Host "Скачиваю Беларусь (~335 МБ)..." -ForegroundColor Cyan
    $url = "https://download.geofabrik.de/europe/belarus-latest.osm.pbf"
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        curl.exe -L --fail --progress-bar -o $pbf $url
    } else {
        Invoke-WebRequest -Uri $url -OutFile $pbf -UseBasicParsing
    }
    $sizeMb = (Get-Item $pbf).Length / 1MB
    if ($sizeMb -lt 100) { throw "Файл подозрительно маленький ($([math]::Round($sizeMb,1)) МБ) — скачивание не удалось" }
}
if (-not (Test-Path $pbf)) {
    throw "Нет файла $pbf. Запустите без -SkipDownload."
}

Write-Host "1/4 Нарезаю область Гродно (bbox $Bbox)..." -ForegroundColor Cyan
& $dockerExe run --rm -v "${abs}:/data" $OsmiumImage `
    osmium extract -b $Bbox -o /data/grodno.osm.pbf --overwrite /data/belarus-latest.osm.pbf

Write-Host "2/4 osrm-extract..." -ForegroundColor Cyan
& $dockerExe run --rm -v "${abs}:/data" $Image `
    osrm-extract -p /opt/car.lua /data/grodno.osm.pbf

Write-Host "3/4 osrm-partition..." -ForegroundColor Cyan
& $dockerExe run --rm -v "${abs}:/data" $Image osrm-partition /data/grodno.osrm

Write-Host "4/4 osrm-customize..." -ForegroundColor Cyan
& $dockerExe run --rm -v "${abs}:/data" $Image osrm-customize /data/grodno.osrm

Write-Host "Готово. Запуск:" -ForegroundColor Green
Write-Host "  docker compose --profile geo up -d"
Write-Host "  curl http://localhost:5000/route/v1/driving/23.82,53.70;23.84,53.68"

