# AI Middle Office - Windows Auto-Start Service Installer (Task Scheduler, no NSSM needed)
# Run as Administrator in PowerShell

[CmdletBinding()]
param(
    [string]$HostAddress = "",
    [switch]$Lan
)

$TaskName = "AI_MiddleOffice"
$TaskPath = "\"
$WorkDir  = $PSScriptRoot
$LogDir   = "$WorkDir\logs"
$Launcher = Join-Path $WorkDir "start_watchdog.ps1"

$principalCheck = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Please run this script from an Administrator PowerShell."
    exit 1
}

# 1. Find Python
$PythonPath = $null
$candidates = @(
    "C:\Users\12521\miniconda3\python.exe",
    "C:\Users\12521\anaconda3\python.exe",
    "$env:USERPROFILE\miniconda3\python.exe",
    "$env:USERPROFILE\anaconda3\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $PythonPath = $c; break }
}
if (-not $PythonPath) {
    Write-Error "python.exe not found."
    exit 1
}
Write-Host "[OK] Python: $PythonPath" -ForegroundColor Green
if (-not (Test-Path $Launcher)) {
    Write-Error "Startup launcher not found: $Launcher"
    exit 1
}

if ($Lan -and $HostAddress -in @("127.0.0.1", "localhost")) {
    Write-Error "-Lan cannot be combined with -HostAddress $HostAddress. Use -Lan alone, -HostAddress 0.0.0.0, or a real LAN IPv4 address."
    exit 1
}

$BindHost = $HostAddress
if (-not $BindHost) {
    if ($Lan) {
        $BindHost = "0.0.0.0"
    } else {
        $BindHost = "127.0.0.1"
    }
}

# 2. Create log directory
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# 3. Remove existing task if present
if (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[INFO] Removing old task..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
}

# 4. Register scheduled task
Write-Host "[Install] Registering scheduled task..." -ForegroundColor Cyan

$watchdogArgs = @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "`"$Launcher`"",
    "-RetryIntervalSeconds",
    "180",
    "-MaxMinutes",
    "60",
    "-HostAddress",
    $BindHost
)
if ($Lan) {
    $watchdogArgs += "-Lan"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($watchdogArgs -join " ") `
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

# 5. Start immediately
Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName).State

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Task state: $state" -ForegroundColor Green
Write-Host " Local URL: http://127.0.0.1:9000/" -ForegroundColor Green
Write-Host " Bind: $BindHost" -ForegroundColor Green
if ($Lan -or $BindHost -ne "127.0.0.1") {
    Write-Host " LAN mode: enabled. After startup, see logs\current_access_urls.txt for the current LAN URL." -ForegroundColor Green
}
Write-Host " Startup mode: watchdog retry every 3 minutes for 60 minutes" -ForegroundColor Green
Write-Host " Logs: $LogDir" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  Start:     Start-ScheduledTask -TaskPath '$TaskPath' -TaskName $TaskName"
Write-Host "  Stop:      Stop-ScheduledTask  -TaskPath '$TaskPath' -TaskName $TaskName"
Write-Host "  Query:     Get-ScheduledTask   -TaskPath '$TaskPath' -TaskName $TaskName"
Write-Host "  Uninstall: Unregister-ScheduledTask -TaskPath '$TaskPath' -TaskName $TaskName -Confirm:`$false"
Write-Host "  View GUI:  taskschd.msc"
