# AI Middle Office - Celery Worker launcher
# Run from PowerShell, or install as a Scheduled Task with install_celery_worker_service.ps1

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$WorkDir = $PSScriptRoot
$LogDir = Join-Path $WorkDir "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

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

Set-Location $WorkDir
$env:TASK_QUEUE_MODE = "celery"
$env:PYTHONUNBUFFERED = "1"

$logFile = Join-Path $LogDir ("celery_worker_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$pidFile = Join-Path $LogDir "celery_worker.pid"

if (Test-Path $pidFile) {
    $oldPidText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($oldProcess) {
            $oldCommand = (Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue).CommandLine
            if ($oldCommand -and $oldCommand -like "*celery*" -and $oldCommand -like "*app.tasks.celery_app*") {
                Write-Host "[INFO] Celery worker already appears to be running with pid: $oldPid"
                exit 0
            }
            Write-Host "[INFO] Removing pid file that points to a non-Celery process: $pidFile"
            Remove-Item $pidFile -Force
        } else {
            Write-Host "[INFO] Removing stale pid file: $pidFile"
            Remove-Item $pidFile -Force
        }
    } else {
        Write-Host "[INFO] Removing invalid pid file: $pidFile"
        Remove-Item $pidFile -Force
    }
}

$existingWorker = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -like "*celery*" -and
        $_.CommandLine -like "*app.tasks.celery_app*" -and
        $_.ProcessId -ne $PID
    } |
    Select-Object -First 1
if ($existingWorker) {
    Write-Host "[INFO] Celery worker already appears to be running with pid: $($existingWorker.ProcessId)"
    Set-Content -Path $pidFile -Value $existingWorker.ProcessId -Encoding ASCII
    exit 0
}

Write-Host "[INFO] WorkDir: $WorkDir"
Write-Host "[INFO] Python: $PythonPath"
Write-Host "[INFO] Log: $logFile"

& $PythonPath -m celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO --pool=solo --concurrency=1 --hostname=quote-worker@%h --logfile="$logFile" --pidfile="$pidFile"
exit $LASTEXITCODE
