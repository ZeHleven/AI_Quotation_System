# AI Middle Office - startup watchdog
# Repeatedly invokes start_all.ps1 until the system becomes ready.

[CmdletBinding()]
param(
    [int]$RetryIntervalSeconds = 180,
    [int]$MaxMinutes = 60,
    [string]$HostAddress = "",
    [switch]$Lan,
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

function Enter-WatchdogInstanceLock {
    param([string]$Path)

    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $content = "pid={0}; started_at={1}`r`n" -f (
            $PID,
            (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        )
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
        $stream.SetLength(0)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        return $stream
    } catch [System.IO.IOException] {
        Write-WatchdogLog (
            "another watchdog instance is already running; " +
            "duplicate exits"
        )
        return $null
    }
}

$WatchdogLockPath = Join-Path $LogDir "startup_watchdog.lock"
$WatchdogLockStream = Enter-WatchdogInstanceLock -Path $WatchdogLockPath
if (-not $WatchdogLockStream) {
    exit 0
}

function Resolve-BindHost {
    param(
        [string]$RequestedHost,
        [switch]$UseLan
    )
    if ($RequestedHost) {
        if ($UseLan -and $RequestedHost -in @("127.0.0.1", "localhost")) {
            throw "-Lan cannot be combined with -HostAddress $RequestedHost."
        }
        return $RequestedHost
    }
    if ($UseLan) { return "0.0.0.0" }
    return "127.0.0.1"
}

function Get-ProbeHost {
    param([string]$BindHost)
    if ($BindHost -in @("0.0.0.0", "::", "", "localhost")) {
        return "127.0.0.1"
    }
    return $BindHost
}

function Test-SystemReady {
    try {
        $response = Invoke-RestMethod -Uri "http://${ProbeHost}:9000/health/ready" -TimeoutSec 5
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
$HostAddress = Resolve-BindHost -RequestedHost $HostAddress -UseLan:$Lan
$ProbeHost = Get-ProbeHost -BindHost $HostAddress
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$attempt = 0

Write-WatchdogLog "watchdog started, retry_interval=${RetryIntervalSeconds}s, max_minutes=${MaxMinutes}, bind=${HostAddress}, probe=${ProbeHost}"

while ((Get-Date) -lt $deadline) {
    $attempt += 1

    if ((Test-SystemReady) -and -not ($Lan -and $HostAddress -eq "0.0.0.0")) {
        Write-WatchdogLog "system already ready; watchdog exits"
        exit 0
    } elseif ($Lan -and $HostAddress -eq "0.0.0.0") {
        Write-WatchdogLog "local health is ready, but LAN mode uses wildcard bind; invoking start_all.ps1 to confirm bind mode"
    }

    Write-WatchdogLog "attempt ${attempt}: invoking start_all.ps1"
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $Launcher, "-NoBrowser", "-HostAddress", $HostAddress)
    if ($Lan) {
        $arguments += "-Lan"
    }
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
$WatchdogLockStream.Dispose()
exit 1
