# AI Middle Office - one-click startup orchestrator
# Starts FastAPI and Celery after CentOS dependencies are reachable.

[CmdletBinding()]
param(
    [string]$CentosHost = "",
    [int]$AppPort = 9000,
    [int]$RemoteTimeoutSeconds = 180,
    [int]$LocalTimeoutSeconds = 120,
    [switch]$NoBrowser,
    [switch]$SkipRemoteWait,
    [switch]$SkipCelery,
    [switch]$SkipMigrations,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$processPath = [Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $processPath) {
    $processPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
}
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
if ($processPath) {
    [Environment]::SetEnvironmentVariable("Path", $processPath, "Process")
}

$WorkDir = $PSScriptRoot
$LogDir = Join-Path $WorkDir "logs"
$EnvFile = Join-Path $WorkDir ".env"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Get-DotEnvValue {
    param(
        [string]$Name,
        [string]$Default = ""
    )
    if (-not (Test-Path $EnvFile)) { return $Default }
    $prefix = "$Name="
    $line = Get-Content -Path $EnvFile -ErrorAction SilentlyContinue |
        Where-Object { $_ -and $_.TrimStart().StartsWith($prefix) } |
        Select-Object -First 1
    if (-not $line) { return $Default }
    $value = $line.Substring($prefix.Length).Trim()
    return $value.Trim('"').Trim("'")
}

function Import-DotEnvToProcess {
    if (-not (Test-Path $EnvFile)) { return }
    Get-Content -Path $EnvFile -ErrorAction SilentlyContinue |
        ForEach-Object {
            $line = $_
            if (-not $line) { return }
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#")) { return }
            $parts = $trimmed.Split("=", 2)
            if ($parts.Count -ne 2) { return }
            $name = $parts[0].Trim().TrimStart([char]0xFEFF)
            if (-not $name) { return }
            $value = $parts[1].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
}

function Find-Python {
    $candidates = @(
        "C:\Users\12521\miniconda3\python.exe",
        "C:\Users\12521\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "python.exe not found. Please install Miniconda or update start_all.ps1."
}

function Test-TcpPort {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutMs = 1500
    )
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $ok) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    } catch {
        if ($client) { $client.Close() }
        return $false
    }
}

function Wait-TcpPort {
    param(
        [string]$Name,
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -TargetHost $TargetHost -Port $Port) {
            Write-Host "[OK] $Name reachable at ${TargetHost}:$Port" -ForegroundColor Green
            return
        }
        Write-Host "[WAIT] $Name ${TargetHost}:$Port ..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
    }
    throw "$Name is not reachable at ${TargetHost}:$Port after ${TimeoutSeconds}s. Check CentOS network, Docker containers, or firewall."
}

function Wait-FastApiReady {
    param([int]$TimeoutSeconds)
    $url = "http://127.0.0.1:$AppPort/health/ready"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $url -TimeoutSec 5
            $lastStatus = $response.status
            if ($response.status -eq "ready") {
                Write-Host "[OK] FastAPI ready: $url" -ForegroundColor Green
                return $response
            }
            Write-Host "[WAIT] FastAPI health is $($response.status)" -ForegroundColor Yellow
        } catch {
            Write-Host "[WAIT] FastAPI health endpoint not ready..." -ForegroundColor Yellow
        }
        Start-Sleep -Seconds 3
    }
    throw "FastAPI did not become ready after ${TimeoutSeconds}s. Last status: $lastStatus"
}

function Start-FastApi {
    param([string]$PythonPath)

    if ((Test-TcpPort -TargetHost "127.0.0.1" -Port $AppPort -TimeoutMs 500) -and -not $Restart) {
        Write-Host "[OK] FastAPI port $AppPort is already listening" -ForegroundColor Green
        return
    }

    $pidFile = Join-Path $LogDir "fastapi.pid"
    if (Test-Path $pidFile) {
        $oldPidText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        $oldPid = 0
        if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
            $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($oldProcess) {
                if ($Restart) {
                    Write-Host "[RESTART] Stopping FastAPI pid from pid file: $oldPid" -ForegroundColor Yellow
                    Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 2
                    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
                    $oldProcess = $null
                }
            }
            if ($oldProcess) {
                $oldCommand = (Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue).CommandLine
                if ($oldCommand -and $oldCommand -like "*uvicorn*" -and $oldCommand -like "*app.main:app*") {
                    Write-Host "[OK] FastAPI already appears to be running with pid: $oldPid" -ForegroundColor Green
                    return
                }
            }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }

    if (Test-TcpPort -TargetHost "127.0.0.1" -Port $AppPort -TimeoutMs 500) {
        throw "FastAPI port $AppPort is still listening after restart cleanup. Stop the old process first, then run start_all.ps1 again."
    }

    $outLog = Join-Path $LogDir ("fastapi_{0}.out.log" -f (Get-Date -Format "yyyyMMdd"))
    $errLog = Join-Path $LogDir ("fastapi_{0}.err.log" -f (Get-Date -Format "yyyyMMdd"))
    $args = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$AppPort")
    $process = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList $args `
        -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $pidFile -Value $process.Id -Encoding ASCII
    Write-Host "[START] FastAPI pid=$($process.Id), logs=$LogDir" -ForegroundColor Cyan
}

function Start-Celery {
    if ($SkipCelery) {
        Write-Host "[SKIP] Celery startup skipped" -ForegroundColor Yellow
        return
    }
    $launcher = Join-Path $WorkDir "start_celery_worker.ps1"
    if (-not (Test-Path $launcher)) {
        throw "Celery launcher not found: $launcher"
    }
    $celeryArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher)
    if ($Restart) {
        $celeryArgs += "-Restart"
    }
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $celeryArgs `
        -WorkingDirectory $WorkDir `
        -WindowStyle Hidden | Out-Null
    Write-Host "[START] Celery worker launcher invoked" -ForegroundColor Cyan
}

function Invoke-DatabaseMigrations {
    param([string]$PythonPath)

    if ($SkipMigrations) {
        Write-Host "[SKIP] Database migrations skipped by parameter" -ForegroundColor Yellow
        return
    }

    $autoRun = (Get-DotEnvValue -Name "AUTO_RUN_DB_MIGRATIONS" -Default "true").ToLowerInvariant()
    if ($autoRun -notin @("1", "true", "yes", "on")) {
        Write-Host "[SKIP] Database migrations skipped because AUTO_RUN_DB_MIGRATIONS is not true" -ForegroundColor Yellow
        return
    }

    $alembicIni = Join-Path $WorkDir "alembic.ini"
    if (-not (Test-Path $alembicIni)) {
        throw "alembic.ini not found: $alembicIni"
    }

    Write-Host "[INFO] Running database migrations..." -ForegroundColor Cyan
    & $PythonPath -c "from alembic.config import main; main()" -c $alembicIni upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migrations failed. Run: pip install -r requirements.txt"
    }
    Write-Host "[OK] Database migrations are up to date" -ForegroundColor Green
}

Set-Location $WorkDir

if (-not $CentosHost) {
    if ($env:MIDDLE_OFFICE_CENTOS_HOST) {
        $CentosHost = $env:MIDDLE_OFFICE_CENTOS_HOST
    } else {
        $CentosHost = "192.168.88.128"
    }
}

$PythonPath = Find-Python
Import-DotEnvToProcess
$minioEnabled = (Get-DotEnvValue -Name "MINIO_ENABLED" -Default "false").ToLowerInvariant()

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " AI Middle Office startup"
Write-Host " WorkDir: $WorkDir"
Write-Host " Python:  $PythonPath"
Write-Host " CentOS:  $CentosHost"
Write-Host "========================================" -ForegroundColor Cyan

if (-not $SkipRemoteWait) {
    Wait-TcpPort -Name "MySQL" -TargetHost $CentosHost -Port 5455 -TimeoutSeconds $RemoteTimeoutSeconds
    Wait-TcpPort -Name "Redis" -TargetHost $CentosHost -Port 6380 -TimeoutSeconds $RemoteTimeoutSeconds
    Wait-TcpPort -Name "RAG service" -TargetHost $CentosHost -Port 8001 -TimeoutSeconds $RemoteTimeoutSeconds
    Wait-TcpPort -Name "n8n" -TargetHost $CentosHost -Port 5678 -TimeoutSeconds $RemoteTimeoutSeconds
    if ($minioEnabled -in @("1", "true", "yes", "on")) {
        Wait-TcpPort -Name "MinIO" -TargetHost $CentosHost -Port 9002 -TimeoutSeconds $RemoteTimeoutSeconds
    } else {
        Write-Host "[SKIP] MinIO wait skipped because MINIO_ENABLED is not true" -ForegroundColor Yellow
    }
} else {
    Write-Host "[SKIP] Remote dependency wait skipped" -ForegroundColor Yellow
}

Invoke-DatabaseMigrations -PythonPath $PythonPath
Start-Celery
Start-FastApi -PythonPath $PythonPath
$ready = Wait-FastApiReady -TimeoutSeconds $LocalTimeoutSeconds

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " System is ready"
Write-Host " URL: http://localhost:$AppPort/"
Write-Host " Queue: $($ready.task_queue.mode) / ok=$($ready.task_queue.ok)"
Write-Host " Logs: $LogDir"
Write-Host "========================================" -ForegroundColor Green

if (-not $NoBrowser) {
    Start-Process "http://localhost:$AppPort/"
}
