# AI Middle Office - Celery Worker Auto-Start Installer (Task Scheduler)
# Run as Administrator in PowerShell after Redis is reachable and dependencies are installed.

$ErrorActionPreference = "Stop"

$TaskName = "AI_MiddleOffice_CeleryWorker"
$TaskPath = "\"
$WorkDir  = $PSScriptRoot
$LogDir   = "$WorkDir\logs"
$Launcher = Join-Path $WorkDir "start_celery_worker.ps1"

$principalCheck = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please run this script from an Administrator PowerShell."
    exit 1
}

if (-not (Test-Path $Launcher)) {
    Write-Error "Worker launcher not found: $Launcher"
    exit 1
}
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

if (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[INFO] Removing old task..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
}

Write-Host "[Install] Registering Celery worker scheduled task..." -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`"" `
    -WorkingDirectory $WorkDir

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName).State

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Celery worker task state: $state" -ForegroundColor Green
Write-Host " Logs: $LogDir" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  Start:     Start-ScheduledTask -TaskPath '$TaskPath' -TaskName $TaskName"
Write-Host "  Stop:      Stop-ScheduledTask  -TaskPath '$TaskPath' -TaskName $TaskName"
Write-Host "  Query:     Get-ScheduledTask   -TaskPath '$TaskPath' -TaskName $TaskName"
Write-Host "  Uninstall: Unregister-ScheduledTask -TaskPath '$TaskPath' -TaskName $TaskName -Confirm:`$false"
