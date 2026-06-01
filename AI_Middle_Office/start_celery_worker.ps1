# AI Middle Office - Celery Worker launcher
# Run from PowerShell, or install as a Scheduled Task with install_celery_worker_service.ps1

[CmdletBinding()]
param(
    [switch]$Restart
)

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

function Get-DotEnvValue {
    param(
        [string]$Name,
        [string]$Default
    )

    $envValue = [Environment]::GetEnvironmentVariable($Name)
    if ($envValue -and $envValue.Trim()) {
        return $envValue.Trim()
    }

    $envFile = Join-Path $WorkDir ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile -ErrorAction SilentlyContinue |
            Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } |
            Select-Object -First 1
        if ($line) {
            $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
            if ($value) { return $value }
        }
    }

    return $Default
}

$WorkerPool = Get-DotEnvValue "CELERY_WORKER_POOL" "threads"
$WorkerConcurrencyText = Get-DotEnvValue "CELERY_WORKER_CONCURRENCY" "2"
$WorkerConcurrency = 2
if (-not [int]::TryParse($WorkerConcurrencyText, [ref]$WorkerConcurrency) -or $WorkerConcurrency -lt 1) {
    $WorkerConcurrency = 2
}
$env:CELERY_WORKER_POOL = $WorkerPool
$env:CELERY_WORKER_CONCURRENCY = [string]$WorkerConcurrency

$logFile = Join-Path $LogDir ("celery_worker_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$pidFile = Join-Path $LogDir "celery_worker.pid"

if (Test-Path $pidFile) {
    $oldPidText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($oldProcess) {
            if ($Restart) {
                Write-Host "[RESTART] Stopping Celery worker pid from pid file: $oldPid"
                Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
                $oldProcess = $null
            }
        }
        if ($oldProcess) {
            $oldCommand = (Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue).CommandLine
            if ($oldCommand -and $oldCommand -like "*celery*" -and $oldCommand -like "*app.tasks.celery_app*") {
                Write-Host "[INFO] Celery worker already appears to be running with pid: $oldPid"
                exit 0
            }
            if (Test-Path $pidFile) {
                Write-Host "[INFO] Removing pid file that points to a non-Celery process: $pidFile"
                Remove-Item $pidFile -Force
            }
        } else {
            Write-Host "[INFO] Removing stale pid file: $pidFile"
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "[INFO] Removing invalid pid file: $pidFile"
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
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
    if ($Restart) {
        Write-Host "[RESTART] Stopping existing Celery worker pid: $($existingWorker.ProcessId)"
        Stop-Process -Id $existingWorker.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    } else {
    Write-Host "[INFO] Celery worker already appears to be running with pid: $($existingWorker.ProcessId)"
    Set-Content -Path $pidFile -Value $existingWorker.ProcessId -Encoding ASCII
    exit 0
    }
}

Write-Host "[INFO] WorkDir: $WorkDir"
Write-Host "[INFO] Python: $PythonPath"
Write-Host "[INFO] Log: $logFile"
Write-Host "[INFO] Worker pool: $WorkerPool"
Write-Host "[INFO] Worker concurrency: $WorkerConcurrency"

& $PythonPath -m celery -A app.tasks.celery_app.celery_app worker --loglevel=INFO --pool=$WorkerPool --concurrency=$WorkerConcurrency --hostname=quote-worker@%h --logfile="$logFile" --pidfile="$pidFile"
exit $LASTEXITCODE
