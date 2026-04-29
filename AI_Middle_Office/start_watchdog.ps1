# AI Middle Office - startup watchdog
# Repeatedly invokes start_all.ps1 until the system becomes ready.

[CmdletBinding()]
param(
    [int]$RetryIntervalSeconds = 180,
    [int]$MaxMinutes = 60,
    [switch]$SkipMigrations
)

$ErrorActionPreference = "Continue"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$WorkDir = $PSScriptRoot
$LogDir = Join-Path $WorkDir "logs"
$Launcher = Join-Path $WorkDir "start_all.ps1"
$WatchdogLog = Join-Path $LogDir ("startup_watchdog_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Write-WatchdogLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $WatchdogLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-SystemReady {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:9000/health/ready" -TimeoutSec 5
        return ($response.status -eq "ready")
    } catch {
        return $false
    }
}

if (-not (Test-Path $Launcher)) {
    Write-WatchdogLog "start_all.ps1 not found: $Launcher"
    exit 1
}

Set-Location $WorkDir
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$attempt = 0

Write-WatchdogLog "watchdog started, retry_interval=${RetryIntervalSeconds}s, max_minutes=${MaxMinutes}"

while ((Get-Date) -lt $deadline) {
    $attempt += 1

    if (Test-SystemReady) {
        Write-WatchdogLog "system already ready; watchdog exits"
        exit 0
    }

    Write-WatchdogLog "attempt ${attempt}: invoking start_all.ps1"
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Launcher, "-NoBrowser")
    if ($SkipMigrations) {
        $arguments += "-SkipMigrations"
    }

    $process = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $arguments `
        -WorkingDirectory $WorkDir `
        -WindowStyle Hidden `
        -PassThru `
        -Wait

    Write-WatchdogLog "attempt ${attempt}: start_all.ps1 exited with code $($process.ExitCode)"

    if (Test-SystemReady) {
        Write-WatchdogLog "system ready after attempt ${attempt}; watchdog exits"
        exit 0
    }

    Start-Sleep -Seconds $RetryIntervalSeconds
}

Write-WatchdogLog "watchdog timed out after ${MaxMinutes} minutes; system is not ready"
exit 1
