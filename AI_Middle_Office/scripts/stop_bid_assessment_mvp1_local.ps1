param(
    [ValidateSet("legacy", "rq2b", "rq2c")]
    [string]$RetrievalMode = "legacy",
    [ValidateSet("view-only", "execute")]
    [string]$AccessMode = "view-only",
    [ValidateSet("deterministic", "deepseek-v4-flash")]
    [string]$ModelMode = "deterministic",
    [string]$LabDirectoryName = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$middleOfficeDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$historicalLabDirectoryName = if ($RetrievalMode -ne "legacy") {
    ".local-mvp1-real-$RetrievalMode"
} elseif ($ModelMode -eq "deepseek-v4-flash") {
    ".local-mvp1-ds-b2"
} else {
    ".local-mvp1"
}
$defaultLabDirectoryName = if ($AccessMode -eq "execute") {
    "$historicalLabDirectoryName-phase4c1"
} else {
    $historicalLabDirectoryName
}
$labDirectoryName = if ($LabDirectoryName) { $LabDirectoryName.Trim() } else { $defaultLabDirectoryName }
if ($labDirectoryName -and $labDirectoryName -notmatch '^\.local-mvp1(?:-[a-z0-9][a-z0-9-]{0,47})?$') {
    throw "LabDirectoryName must be a local .local-mvp1[-name] directory"
}
$logStem = if ($LabDirectoryName -or $AccessMode -eq "execute") {
    "bid-" + $labDirectoryName.TrimStart(".")
} elseif ($RetrievalMode -ne "legacy") {
    "bid-mvp1-real"
} else {
    "bid-mvp1-local"
}
$pidFile = Join-Path $middleOfficeDir "logs\$logStem.pid"

function Get-LocalListenerProcessId([int]$ListenerPort) {
    if ($ListenerPort -le 0) { return $null }
    $pattern = "^\s*TCP\s+127\.0\.0\.1:$ListenerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match $pattern) { return [int]$Matches[1] }
    }
    return $null
}

$processId = if (Test-Path -LiteralPath $pidFile) {
    [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
} else {
    Get-LocalListenerProcessId -ListenerPort $Port
}
if (-not $processId) {
    Write-Output "MVP-1 local lab is not running"
    exit 0
}
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output "MVP-1 local lab is already stopped"
    exit 0
}
if ($process.ProcessName -notin @("python", "python3", "uvicorn")) {
    throw "PID $processId is not a Python/Uvicorn process; refusing to stop it"
}
$commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue).CommandLine
if ($Port -gt 0) {
    $portPattern = '--port[\s"]+' + [regex]::Escape([string]$Port) + '(?:[\s"]|$)'
    $commandMatches = (
        -not [string]::IsNullOrWhiteSpace($commandLine) -and
        $commandLine -match 'app\.mvp1_local:app' -and
        $commandLine -match $portPattern
    )
    $stderrLog = Join-Path $middleOfficeDir "logs\$logStem.err.log"
    $logMatches = $false
    if (Test-Path -LiteralPath $stderrLog -PathType Leaf) {
        $logTail = (Get-Content -LiteralPath $stderrLog -Tail 80) -join "`n"
        $logMatches = (
            $logTail -match "Started server process \[$processId\]" -and
            $logTail -match "Uvicorn running on http://127\.0\.0\.1:$Port"
        )
    }
    $healthMatches = $false
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -TimeoutSec 3
        $healthMatches = (
            $health.status -eq "ok" -and
            [string]$health.mode -match '^isolated-local-(view-only|execute)-'
        )
    } catch {
        $healthMatches = $false
    }
    if (-not $commandMatches -and -not $logMatches -and -not $healthMatches) {
        throw "PID $processId does not match the requested MVP-1 local port; refusing to stop it"
    }
}
Stop-Process -Id $processId -Force
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Output "MVP-1 local lab stopped (PID $processId)"
