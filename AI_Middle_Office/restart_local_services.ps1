# Restart local Windows-side services: Celery worker + FastAPI backend.
# This is the daily shortcut for local development after code changes.
#
# Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restart_local_services.ps1
#
# Optional:
#   .\restart_local_services.ps1 -RunMigrations
#   .\restart_local_services.ps1 -OpenBrowser
#   .\restart_local_services.ps1 -SkipCelery

[CmdletBinding()]
param(
    [int]$AppPort = 9000,
    [string]$HostAddress = "127.0.0.1",
    [int]$TimeoutSeconds = 90,
    [switch]$RunMigrations,
    [switch]$OpenBrowser,
    [switch]$SkipCelery
)

$ErrorActionPreference = "Stop"

$WorkDir = $PSScriptRoot
$CeleryLauncher = Join-Path $WorkDir "start_celery_worker.ps1"
$BackendLauncher = Join-Path $WorkDir "restart_backend.ps1"

if (-not (Test-Path $BackendLauncher)) {
    throw "Backend restart script not found: $BackendLauncher"
}

Set-Location $WorkDir

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Restart local services"
Write-Host " WorkDir: $WorkDir"
Write-Host " Services: FastAPI$(if ($SkipCelery) { '' } else { ' + Celery' })"
Write-Host "========================================" -ForegroundColor Cyan

if ($SkipCelery) {
    Write-Host "[SKIP] Celery restart skipped" -ForegroundColor Yellow
} else {
    if (-not (Test-Path $CeleryLauncher)) {
        throw "Celery restart script not found: $CeleryLauncher"
    }
    Write-Host "[RESTART] Celery worker" -ForegroundColor Cyan
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $CeleryLauncher, "-Restart") `
        -WorkingDirectory $WorkDir `
        -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 2
}

$backendArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $BackendLauncher,
    "-AppPort",
    "$AppPort",
    "-HostAddress",
    $HostAddress,
    "-TimeoutSeconds",
    "$TimeoutSeconds"
)
if ($RunMigrations) {
    $backendArgs += "-RunMigrations"
} else {
    Write-Host "[SKIP] Database migrations skipped. Use -RunMigrations after schema changes." -ForegroundColor Yellow
}
if ($OpenBrowser) {
    $backendArgs += "-OpenBrowser"
}

Write-Host "[RESTART] FastAPI backend" -ForegroundColor Cyan
& powershell.exe @backendArgs
if ($LASTEXITCODE -ne 0) {
    throw "FastAPI restart failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Local services restarted"
Write-Host " URL: http://${HostAddress}:$AppPort/"
Write-Host " Logs: $(Join-Path $WorkDir 'logs')"
Write-Host "========================================" -ForegroundColor Green
