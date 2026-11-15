# -*- coding: utf-8 -*-
# Проверка стека MilkLogistic: состояние API, матрица, честный расчёт плана.
# Запускается из go.bat (или вручную: powershell -File check_plan.ps1)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Line([string]$t) { Write-Host $t }

# Windows PowerShell 5.1 читает JSON с сервера как Latin-1, если в Content-Type
# нет charset. Забираем сырые байты и декодируем их как UTF-8 сами.
function Get-Json([string]$url, [string]$method = "GET", [string]$body = $null) {
    $params = @{ Uri = $url; Method = $method; TimeoutSec = 300; UseBasicParsing = $true }
    if ($body) {
        $params.Body = $body
        $params.ContentType = "application/json; charset=utf-8"
    }
    $response = Invoke-WebRequest @params
    $text = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
    return $text | ConvertFrom-Json
}

try {
    $h = Get-Json "http://localhost:8000/api/health"
    $matrix = if ($h.matrix.ready) { "готова $($h.matrix.size)x$($h.matrix.size) ($($h.matrix.provider))" } else { "НЕ ГОТОВА" }
    Line ("Состояние API: " + $h.status + " | Матрица OSRM: " + $matrix)
} catch {
    Line ("ОШИБКА: API не отвечает на /api/health: " + $_.Exception.Message)
    exit 1
}

try {
    $r = Get-Json "http://localhost:8000/api/optimize" -method "POST" -body "{}"
} catch {
    Line ("ОШИБКА расчёта плана: " + $_.Exception.Message)
    if ($_.ErrorDetails) { Line $_.ErrorDetails.Message }
    exit 1
}

$t = $r.totals
Line ""
Line ("Маршрутов: " + $r.routes.Count + " | Пробег: " + [math]::Round($t.distance_m / 1000, 1) + " км")
Line ("Развезено: " + $t.assigned_kg + " из " + $t.demand_kg + " кг | НЕ развезено: " + $t.unassigned_kg + " кг")
Line ("Машин: " + $t.vehicles_used + " | вместимость парка: " + $t.capacity_kg + " кг")
Line ""

$first = $r.routes[0]
$h1 = [math]::Floor($first.work_minutes / 60)
$m1 = $first.work_minutes - 60 * $h1
Line ("Рейс 1: " + $first.vehicle_id + " | работа " + $h1 + " ч " + $m1 + " мин | возврат " + $first.end_time)
$first.stops | Select-Object -First 6 | ForEach-Object {
    Line ("  приб. " + $_.arrive + " | выезд " + $_.leave + " | перегон " + $_.travel_minutes + " мин / " + [math]::Round($_.travel_m / 1000, 1) + " км | " + $_.address)
}

# Проверка честности времени: перегон должен реально двигать часы.
$teleports = 0
foreach ($route in $r.routes) {
    $prevLeave = $null
    foreach ($stop in $route.stops) {
        if ($prevLeave -ne $null -and $stop.arrive -eq $prevLeave -and $stop.travel_minutes -gt 0) {
            $teleports++
        }
        $prevLeave = $stop.leave
    }
}
Line ""
if ($teleports -eq 0) {
    Line "ПРОВЕРКА ЧЕСТНОСТИ ПРОЙДЕНА: перегоны двигают время, телепортов нет."
} else {
    Line ("ПРОВЕРКА НЕ ПРОЙДЕНА: телепортов " + $teleports + " (прибытие = выезду при ненулевом перегоне).")
    exit 1
}
Line ""
Line "Интерфейс: http://localhost:8080 | Отчёт: http://localhost:8000/"
exit 0
