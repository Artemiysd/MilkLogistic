<#
  Остановка всего стека. Данные и образы остаются, следующий запуск — .\start.ps1
#>
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$dockerExe = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $dockerExe) {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )) {
        if (Test-Path $candidate) { $dockerExe = $candidate; break }
    }
}
if (-not $dockerExe) { Write-Host "Docker не найден" -ForegroundColor Red; exit 1 }
$env:PATH = "$(Split-Path $dockerExe);$env:PATH"

& $dockerExe compose --profile geo --profile tools down
Write-Host "`nОстановлено. Запуск снова: .\start.ps1" -ForegroundColor Green
