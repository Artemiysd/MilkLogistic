<#
  Проверка своего OSRM и связки routing -> OSRM.

    .\infra\osrm\verify.ps1          # быстрые проверки (3 точки)
    .\infra\osrm\verify.ps1 -Full    # все 215 точек + сверка с матрицей (нужен интернет не нужен)

  Запускать из корня репозитория при поднятом стеке.
#>
param(
    [switch]$Full
)

$ErrorActionPreference = "Continue"
$script:bad = 0

function Ok([string]$message) { Write-Host "  [OK]   $message" -ForegroundColor Green }
function Fail([string]$message) { Write-Host "  [FAIL] $message" -ForegroundColor Red; $script:bad++ }

Write-Host "`n1. OSRM напрямую (порт 5000)" -ForegroundColor Cyan
try {
    $route = Invoke-RestMethod "http://localhost:5000/route/v1/driving/23.828288,53.708928;23.9116412,53.6753356?overview=false" -TimeoutSec 15
    if ($route.code -eq "Ok") {
        Ok ("маршрут склад -> точка 1: {0:N2} км, {1:N0} мин" -f ($route.routes[0].distance / 1000), ($route.routes[0].duration / 60))
    } else {
        Fail "OSRM ответил code=$($route.code)"
    }
} catch {
    Fail "OSRM на 5000 не отвечает: $($_.Exception.Message)"
}

Write-Host "`n2. Таблица (проверяем --max-table-size)" -ForegroundColor Cyan
try {
    $cacheForTable = Get-Content "data/matrix_cache.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    $sample = @($cacheForTable.points | Select-Object -First 120)
    # важно: инвариантная культура, иначе в ru-RU координаты станут "23,828" и OSRM ответит 400
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $coords = ($sample | ForEach-Object { "{0},{1}" -f $_.lon.ToString($inv), $_.lat.ToString($inv) }) -join ";"
    # конкатенация, а не интерполяция: в PowerShell "$coords?... " съедает переменную
    $tableUrl = "http://localhost:5000/table/v1/driving/" + $coords + "?annotations=distance"
    $table = Invoke-RestMethod $tableUrl -TimeoutSec 60
    if ($table.code -eq "Ok") {
        Ok ("матрица {0}x{0} посчитана — лимит таблицы снят" -f $sample.Count)
    } else {
        Fail "table code=$($table.code)"
    }
} catch {
    $detail = ""
    if ($_.Exception.Response) {
        $reader = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
        $detail = $reader.ReadToEnd()
    }
    Fail "table не работает: $detail (проверьте --max-table-size)"
}

Write-Host "`n3. Сервис маршрутизации смотрит на свой OSRM" -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod "http://localhost:8100/api/health" -TimeoutSec 30
    if ($health.osrm.reachable -and $health.osrm.base_url -like "*osrm:5000*") {
        Ok "routing -> $($health.osrm.base_url), задержка $($health.osrm.latency_ms) мс"
    } else {
        Fail "routing смотрит на $($health.osrm.base_url) (нужно http://osrm:5000). Поменяйте OSRM_BASE_URL в .env и перезапустите routing"
    }
} catch {
    Fail "routing на 8100 не отвечает: $($_.Exception.Message)"
}

Write-Host "`n4. Маршрут через сервис маршрутизации" -ForegroundColor Cyan
$body = @{
    points = @(
        @{ id = "depot";  lat = 53.708928;  lon = 23.828288 },
        @{ id = "point1"; lat = 53.6753356; lon = 23.9116412 }
    )
} | ConvertTo-Json -Depth 5
try {
    $response = Invoke-RestMethod "http://localhost:8100/api/calculate-route" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 60
    Ok ("routing вернул {0:N2} км, геометрия: {1} точек" -f ($response.distance_m / 1000), $response.geometry.coordinates.Count)
} catch {
    Fail "calculate-route: $($_.Exception.Message)"
}

if ($Full) {
    Write-Host "`n5. Все 215 точек: маршрут против матрицы" -ForegroundColor Cyan
    $cache = Get-Content "data/matrix_cache.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    $points = @($cache.points | ForEach-Object { @{ id = $_.id; lat = $_.lat; lon = $_.lon } })
    $payload = @{ points = $points } | ConvertTo-Json -Depth 5
    $payloadPath = Join-Path $env:TEMP "verify_route_all.json"
    [IO.File]::WriteAllText($payloadPath, $payload, (New-Object System.Text.UTF8Encoding($false)))

    $expected = 0.0
    for ($i = 0; $i -lt $cache.size - 1; $i++) { $expected += $cache.distance_m[$i][$i + 1] }

    $started = Get-Date
    $result = Invoke-RestMethod "http://localhost:8100/api/calculate-route" -Method Post -InFile $payloadPath -ContentType "application/json" -TimeoutSec 300
    $seconds = ((Get-Date) - $started).TotalSeconds

    $delta = [math]::Abs($result.distance_m - $expected)
    Write-Host ("  маршрут: {0:N2} км, матрица: {1:N2} км, расхождение {2:N1} м, {3:N1} с, запросов к OSRM: {4}" -f `
        ($result.distance_m / 1000), ($expected / 1000), $delta, $seconds, $result.requests)
    if ($delta -le 1) { Ok "расхождения нет — цифры у алгоритма и в интерфейсе совпадут" }
    elseif ($delta -le 50) { Ok "расхождение в пределах метража снимков OSM — допустимо" }
    else { Fail "расхождение $([math]::Round($delta)) м: матрица собрана на другом OSRM, пересоберите её" }
}

Write-Host ""
if ($script:bad -eq 0) {
    Write-Host "Итог: всё ок" -ForegroundColor Green
} else {
    Write-Host "Итог: проблем — $($script:bad)" -ForegroundColor Yellow
    exit 1
}
