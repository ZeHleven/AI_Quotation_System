param(
    [int]$Port = 9000,
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"

$middleOfficeDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryDir = (Resolve-Path (Join-Path $middleOfficeDir "..")).Path
$distIndex = Join-Path $repositoryDir "ai-web\dist\index.html"
$logDir = Join-Path $middleOfficeDir "logs"
$stdoutLog = Join-Path $logDir "bid-mvp0-preview.out.log"
$stderrLog = Join-Path $logDir "bid-mvp0-preview.err.log"
$pidFile = Join-Path $logDir "bid-mvp0-preview.pid"

if (-not (Test-Path -LiteralPath $distIndex)) {
    throw "ai-web/dist/index.html is missing; build the Vite frontend first"
}

function Get-LocalListenerProcessId([int]$ListenerPort) {
    $pattern = "^\s*TCP\s+127\.0\.0\.1:$ListenerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match $pattern) {
            return [int]$Matches[1]
        }
    }
    return $null
}

$existingListenerPid = Get-LocalListenerProcessId -ListenerPort $Port
if ($existingListenerPid) {
    throw "127.0.0.1:$Port is already in use by PID $existingListenerPid"
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$processPath = [System.Environment]::GetEnvironmentVariable("Path", "Process")
Remove-Item Env:Path -ErrorAction SilentlyContinue
Remove-Item Env:PATH -ErrorAction SilentlyContinue
$env:Path = $processPath
$env:BID_MVP0_PREVIEW_BIND_HOST = "127.0.0.1"
$arguments = @(
    "-m", "uvicorn", "app.mvp0_preview:app",
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--no-access-log"
)
$process = Start-Process `
    -FilePath $PythonExecutable `
    -ArgumentList $arguments `
    -WorkingDirectory $middleOfficeDir `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

$listenerPid = $null
for ($attempt = 0; $attempt -lt 40; $attempt += 1) {
    Start-Sleep -Milliseconds 125
    $startedListenerPid = Get-LocalListenerProcessId -ListenerPort $Port
    if ($startedListenerPid) {
        $listenerPid = $startedListenerPid
        break
    }
    if ($process.HasExited) {
        break
    }
}
if (-not $listenerPid) {
    throw "MVP-0 preview did not start; inspect $stderrLog"
}

Set-Content -LiteralPath $pidFile -Value $listenerPid -Encoding ascii
Write-Output "MVP-0 isolated preview started: http://127.0.0.1:$Port/admin/bid-assessment-runtime-lab (PID $listenerPid)"
