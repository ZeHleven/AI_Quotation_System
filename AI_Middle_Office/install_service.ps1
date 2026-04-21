# AI Middle Office - Windows Auto-Start Service Installer (Task Scheduler, no NSSM needed)
# Run as Administrator in PowerShell

$TaskName = "AI_MiddleOffice"
$WorkDir  = "C:\Users\12521\Desktop\Clear_test\AI_Middle_Office"
$LogDir   = "$WorkDir\logs"

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

# 2. Create log directory
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# 3. Remove existing task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[INFO] Removing old task..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 4. Register scheduled task
Write-Host "[Install] Registering scheduled task..." -ForegroundColor Cyan

$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "-m uvicorn app.main:app --port 9000" `
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
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

# 5. Start immediately
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $TaskName).State

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Task state: $state" -ForegroundColor Green
Write-Host " URL: http://localhost:9000/" -ForegroundColor Green
Write-Host " Logs: $LogDir" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  Start:     Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Stop:      Stop-ScheduledTask  -TaskName $TaskName"
Write-Host "  Uninstall: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host "  View GUI:  taskschd.msc"
