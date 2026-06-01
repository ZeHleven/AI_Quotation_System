# Restart only the local FastAPI backend.
# Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restart_backend.ps1
# Optional:
#   .\restart_backend.ps1 -AppPort 9000 -RunMigrations -OpenBrowser

[CmdletBinding()]
param(
    [int]$AppPort = 9000,
    [string]$HostAddress = "127.0.0.1",
    [string]$PythonPath = "",
    [int]$TimeoutSeconds = 90,
    [switch]$RunMigrations,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$WorkDir = $PSScriptRoot
$LogDir = Join-Path $WorkDir "logs"
$EnvFile = Join-Path $WorkDir ".env"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

function Find-Python {
    if ($PythonPath -and (Test-Path $PythonPath)) {
        return $PythonPath
    }
    $candidates = @(
        "C:\Users\12521\miniconda3\python.exe",
        "C:\Users\12521\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "python.exe not found. Pass -PythonPath or update this script."
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

function Get-ListeningProcessIds {
    param([int]$Port)

    $ids = @()
    try {
        $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        $ids += $connections | Select-Object -ExpandProperty OwningProcess
    } catch {
        $netstat = netstat -ano -p tcp | Select-String -Pattern "LISTENING"
        foreach ($line in $netstat) {
            $text = $line.ToString().Trim()
            if ($text -match "[:\.]$Port\s+.*\s+LISTENING\s+(\d+)$") {
                $ids += [int]$Matches[1]
            }
        }
    }

    return $ids | Where-Object { $_ -and $_ -gt 0 } | Sort-Object -Unique
}

function Stop-FastApiByPidFile {
    $pidFile = Join-Path $LogDir "fastapi.pid"
    if (-not (Test-Path $pidFile)) { return }

    $oldPidText = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    $oldPid = 0
    if (-not [int]::TryParse($oldPidText, [ref]$oldPid)) {
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        return
    }

    $process = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "[STOP] FastAPI pid from pid file: $oldPid" -ForegroundColor Yellow
        Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

function Stop-ProcessesOnPort {
    param([int]$Port)

    $processIds = Get-ListeningProcessIds -Port $Port
    foreach ($processId in $processIds) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue).CommandLine
        Write-Host "[STOP] Port $Port owner pid=$processId name=$($process.ProcessName)" -ForegroundColor Yellow
        if ($commandLine) {
            Write-Host "       $commandLine" -ForegroundColor DarkGray
        }
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    if ($processIds.Count -gt 0) {
        Start-Sleep -Seconds 2
    }
}

function Invoke-DatabaseMigrations {
    param([string]$ResolvedPythonPath)

    if (-not $RunMigrations) {
        Write-Host "[SKIP] Database migrations skipped. Use -RunMigrations when needed." -ForegroundColor Yellow
        return
    }

    $alembicIni = Join-Path $WorkDir "alembic.ini"
    if (-not (Test-Path $alembicIni)) {
        throw "alembic.ini not found: $alembicIni"
    }

    Write-Host "[INFO] Running database migrations..." -ForegroundColor Cyan
    & $ResolvedPythonPath -c "from alembic.config import main; main()" -c $alembicIni upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migrations failed."
    }
    Write-Host "[OK] Database migrations are up to date" -ForegroundColor Green
}

function Start-FastApi {
    param([string]$ResolvedPythonPath)

    $outLog = Join-Path $LogDir ("fastapi_{0}.out.log" -f (Get-Date -Format "yyyyMMdd"))
    $errLog = Join-Path $LogDir ("fastapi_{0}.err.log" -f (Get-Date -Format "yyyyMMdd"))
    $args = @("-m", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", "$AppPort")

    $process = Start-Process `
        -FilePath $ResolvedPythonPath `
        -ArgumentList $args `
        -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden `
        -PassThru

    Set-Content -Path (Join-Path $LogDir "fastapi.pid") -Value $process.Id -Encoding ASCII
    Write-Host "[START] FastAPI pid=$($process.Id)" -ForegroundColor Cyan
    Write-Host "[LOGS]  $LogDir" -ForegroundColor Cyan
}

function Wait-FastApiReady {
    $readyUrl = "http://${HostAddress}:$AppPort/health/ready"
    $liveUrl = "http://${HostAddress}:$AppPort/health/live"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ""

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $readyUrl -TimeoutSec 5
            if ($response.status -eq "ready") {
                Write-Host "[OK] FastAPI ready: $readyUrl" -ForegroundColor Green
                return
            }
            $lastError = "ready endpoint status=$($response.status)"
            Write-Host "[WAIT] $lastError" -ForegroundColor Yellow
        } catch {
            $lastError = $_.Exception.Message
            try {
                Invoke-RestMethod -Uri $liveUrl -TimeoutSec 3 | Out-Null
                Write-Host "[WAIT] FastAPI is live, waiting for ready dependencies..." -ForegroundColor Yellow
            } catch {
                Write-Host "[WAIT] FastAPI not reachable yet..." -ForegroundColor Yellow
            }
        }
        Start-Sleep -Seconds 2
    }

    throw "FastAPI did not become ready after ${TimeoutSeconds}s. Last error: $lastError"
}

Set-Location $WorkDir
$ResolvedPythonPath = Find-Python
Import-DotEnvToProcess

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Restart FastAPI backend"
Write-Host " WorkDir: $WorkDir"
Write-Host " Python:  $ResolvedPythonPath"
Write-Host " URL:     http://${HostAddress}:$AppPort/"
Write-Host "========================================" -ForegroundColor Cyan

Stop-FastApiByPidFile
Stop-ProcessesOnPort -Port $AppPort
Invoke-DatabaseMigrations -ResolvedPythonPath $ResolvedPythonPath
Start-FastApi -ResolvedPythonPath $ResolvedPythonPath
Wait-FastApiReady

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Backend restarted"
Write-Host " URL: http://${HostAddress}:$AppPort/"
Write-Host " Logs: $LogDir"
Write-Host "========================================" -ForegroundColor Green

if ($OpenBrowser) {
    Start-Process "http://${HostAddress}:$AppPort/"
}
