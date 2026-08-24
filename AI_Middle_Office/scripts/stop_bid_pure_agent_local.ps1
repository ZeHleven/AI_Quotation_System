param(
    [string]$DataDirectoryName = ".local-pure-agent-daily",
    [ValidateRange(1024, 65535)]
    [int]$Port = 9018
)

$ErrorActionPreference = "Stop"
if ($DataDirectoryName -notmatch '^\.local-pure-agent-daily(?:-[a-z0-9][a-z0-9-]{0,31})?$') {
    throw "DataDirectoryName must be .local-pure-agent-daily[-name]"
}
$middleOfficeDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidFile = Join-Path (Join-Path $middleOfficeDir $DataDirectoryName) "server.pid"
if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    Write-Host "Pure Agent local service is not running."
    return
}
$processId = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "Removed a stale Pure Agent local PID file."
    return
}
function Get-LoopbackListenerProcessId([int]$ListenerPort) {
    $pattern = "^\s*TCP\s+127\.0\.0\.1:$ListenerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match $pattern) { return [int]$Matches[1] }
    }
    return $null
}
$listenerPid = Get-LoopbackListenerProcessId -ListenerPort $Port
if ($listenerPid -ne $processId -or $process.ProcessName -notmatch '^pythonw?$') {
    throw "PID file does not belong to the Pure Agent local entry; process was not stopped"
}
Stop-Process -Id $processId
Remove-Item -LiteralPath $pidFile -Force
Write-Host "Pure Agent local service stopped."
