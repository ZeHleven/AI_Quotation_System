[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 2147483647)]
    [int]$ExpectedPid
)

$ErrorActionPreference = "Stop"
$workDir = Split-Path -Parent $PSScriptRoot

$listenerPattern = "^\s*TCP\s+127\.0\.0\.1:9000\s+.*LISTENING\s+$ExpectedPid\s*$"
$listener = netstat -ano | Select-String -Pattern $listenerPattern
if (-not $listener) {
    throw "PID $ExpectedPid is no longer the verified 127.0.0.1:9000 listener"
}

$process = Get-Process -Id $ExpectedPid -ErrorAction Stop
if ($process.ProcessName -ne "python") {
    throw "Verified listener PID $ExpectedPid is not a Python process"
}

Stop-Process -Id $ExpectedPid -Force -ErrorAction Stop
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $stillListening = netstat -ano | Select-String -Pattern ":9000\s+.*LISTENING"
    if (-not $stillListening) {
        break
    }
    Start-Sleep -Milliseconds 500
}
if (netstat -ano | Select-String -Pattern ":9000\s+.*LISTENING") {
    throw "Port 9000 remained occupied after stopping PID $ExpectedPid"
}

Set-Location $workDir
& powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File (Join-Path $workDir "start_all.ps1") `
    -Restart `
    -NoBrowser `
    -SkipMigrations
if ($LASTEXITCODE -ne 0) {
    throw "start_all.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "SECURITY_PHASE1_WINDOWS_RESTART_OK" -ForegroundColor Green
