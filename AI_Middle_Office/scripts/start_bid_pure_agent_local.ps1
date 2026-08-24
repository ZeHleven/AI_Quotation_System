param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 9018,
    [string]$PythonExecutable = "",
    [string]$DataDirectoryName = ".local-pure-agent-daily",
    [string]$PdfPath = "",
    [string]$EmbeddingModelPath = "C:\Users\12521\.cache\huggingface\hub\models--maidalun1020--bce-embedding-base_v1\snapshots\9c0d82af44af61abe171ffae23fde5740c0ec1a8",
    [string]$SecretEnvFile = "",
    [switch]$InitializeLocalDatabase,
    [switch]$PreflightOnly,
    [switch]$ReplaceLocalInstance
)

$ErrorActionPreference = "Stop"
$middleOfficeDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryDir = (Resolve-Path (Join-Path $middleOfficeDir "..")).Path
if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $PythonExecutable = Join-Path $middleOfficeDir ".tmp\phase4d2-test-venv\Scripts\python.exe"
}
if ($DataDirectoryName -notmatch '^\.local-pure-agent-daily(?:-[a-z0-9][a-z0-9-]{0,31})?$') {
    throw "DataDirectoryName must be .local-pure-agent-daily[-name]"
}

$dataRoot = Join-Path $middleOfficeDir $DataDirectoryName
$databasePath = Join-Path $dataRoot "runtime.db"
$secretPath = Join-Path $dataRoot ".continuation-secret"
$stdoutLog = Join-Path $dataRoot "server.out.log"
$stderrLog = Join-Path $dataRoot "server.err.log"
$pidFile = Join-Path $dataRoot "server.pid"
$lockedDependencyPath = Join-Path $middleOfficeDir ".tmp\rq2-locked-runtime"
$resolvedPdf = if ($PdfPath) {
    $PdfPath
} else {
    Join-Path $middleOfficeDir ".local-mvp1-real-pdf-sources\香港中心.pdf"
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "PythonExecutable does not exist"
}
if ([string]::IsNullOrWhiteSpace($SecretEnvFile)) {
    throw "SecretEnvFile is required for the explicit local Runtime"
}
if (-not (Test-Path -LiteralPath $resolvedPdf -PathType Leaf)) {
    throw "Frozen local PDF is missing"
}
if (-not (Test-Path -LiteralPath $EmbeddingModelPath -PathType Container)) {
    throw "Frozen BCE embedding snapshot is missing"
}
if (-not (Test-Path -LiteralPath $SecretEnvFile -PathType Leaf)) {
    throw "SecretEnvFile does not exist"
}
if (-not (Test-Path -LiteralPath $lockedDependencyPath -PathType Container)) {
    throw "Frozen local Python dependency set is missing"
}

function New-LocalContinuationSecret {
    $bytes = New-Object byte[] 48
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

$continuationSecretWasGenerated = $false
if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
    $continuationSecret = (Get-Content -LiteralPath $secretPath -Raw).Trim()
} else {
    $continuationSecret = New-LocalContinuationSecret
    $continuationSecretWasGenerated = $true
}
if ($InitializeLocalDatabase -and $continuationSecretWasGenerated) {
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    [IO.File]::WriteAllText(
        $secretPath,
        $continuationSecret,
        (New-Object Text.UTF8Encoding($false))
    )
}
if ([Text.Encoding]::UTF8.GetByteCount($continuationSecret) -lt 32) {
    throw "Local Continuation Secret is invalid"
}

$resolvedDataRoot = [IO.Path]::GetFullPath($dataRoot)
$resolvedPdf = (Resolve-Path -LiteralPath $resolvedPdf).Path
$resolvedEmbedding = (Resolve-Path -LiteralPath $EmbeddingModelPath).Path
$resolvedSecretEnv = (Resolve-Path -LiteralPath $SecretEnvFile).Path
$databaseUrl = "sqlite:///" + ($databasePath -replace '\\', '/')

$env:APP_ENV = "local"
$env:PUBLIC_ACCESS_ENABLED = "false"
$env:DATABASE_URL = $databaseUrl
$env:MIGRATION_DATABASE_URL = $databaseUrl
$env:AUTO_CREATE_TABLES = "false"
$env:AUTO_RUN_DB_MIGRATIONS = "false"
$env:STARTUP_COMPAT_MIGRATIONS = "false"
$env:FEATURE_VITE_FRONTEND = "true"
$env:FEATURE_BID_ASSESSMENT_PURE_AGENT = "true"
$env:FEATURE_BID_ASSESSMENT_PURE_AGENT_RUNTIME = "true"
$env:FEATURE_BID_ASSESSMENT_V1_RUNTIME = "false"
$env:BID_ASSESSMENT_PURE_AGENT_CONTINUATION_SECRET = $continuationSecret
$env:BID_PURE_AGENT_LOCAL_ACTIVATION = "explicit"
$env:BID_PURE_AGENT_LOCAL_BIND_HOST = "127.0.0.1"
$env:BID_PURE_AGENT_LOCAL_DATA_ROOT = $resolvedDataRoot
$env:BID_PURE_AGENT_LOCAL_DATABASE = $databasePath
$env:BID_PURE_AGENT_LOCAL_PDF = $resolvedPdf
$env:BID_PURE_AGENT_LOCAL_EMBEDDING_MODEL = $resolvedEmbedding
$env:BID_PURE_AGENT_LOCAL_SECRET_ENV_FILE = $resolvedSecretEnv
$env:BID_PURE_AGENT_LOCAL_PROVIDER_CHAT_URL = "https://api.deepseek.com/chat/completions"
$env:BID_PURE_AGENT_LOCAL_EXTERNAL_MCP = "false"
$env:BID_PURE_AGENT_LOCAL_MILVUS = "false"
$env:BID_PURE_AGENT_LOCAL_OCR_VISION = "false"
$env:TASK_QUEUE_MODE = "local"
$env:READY_CHECK_EXTERNAL_SERVICES = "false"
$env:ALLOW_SELF_REGISTRATION = "true"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$existingPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($existingPythonPath)) {
    $lockedDependencyPath
} else {
    "$lockedDependencyPath;$existingPythonPath"
}

if ($InitializeLocalDatabase) {
    Push-Location $middleOfficeDir
    try {
        & $PythonExecutable scripts/initialize_bid_pure_agent_local_database.py --database $databasePath
        if ($LASTEXITCODE -ne 0) { throw "Local schema snapshot initialization failed" }
    } finally {
        Pop-Location
    }
}

Push-Location $middleOfficeDir
try {
    & $PythonExecutable scripts/preflight_bid_pure_agent_local.py
    if ($LASTEXITCODE -ne 0) { throw "Pure Agent local Preflight failed" }
} finally {
    Pop-Location
}
if ($PreflightOnly) {
    Write-Host "Pure Agent local Preflight passed; no service, model, or business input was started."
    return
}

if ($continuationSecretWasGenerated) {
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    [IO.File]::WriteAllText(
        $secretPath,
        $continuationSecret,
        (New-Object Text.UTF8Encoding($false))
    )
}

function Get-LoopbackListenerProcessId([int]$ListenerPort) {
    $pattern = "^\s*TCP\s+127\.0\.0\.1:$ListenerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match $pattern) { return [int]$Matches[1] }
    }
    return $null
}

if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
    $knownPid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
    $knownProcess = Get-Process -Id $knownPid -ErrorAction SilentlyContinue
    if ($knownProcess) {
        $listenerPid = Get-LoopbackListenerProcessId -ListenerPort $Port
        $isOwned = $listenerPid -eq $knownPid -and $knownProcess.ProcessName -match '^pythonw?$'
        if (-not $ReplaceLocalInstance -or -not $isOwned) {
            throw "A local process already owns the Pure Agent PID file"
        }
        Stop-Process -Id $knownPid -Force
    }
    Remove-Item -LiteralPath $pidFile -Force
}

$arguments = @(
    "-m", "uvicorn", "app.pure_agent_local:create_app", "--factory",
    "--host", "127.0.0.1", "--port", "$Port", "--workers", "1"
)
$startParameters = @{
    FilePath = $PythonExecutable
    ArgumentList = $arguments
    WorkingDirectory = $middleOfficeDir
    RedirectStandardOutput = $stdoutLog
    RedirectStandardError = $stderrLog
    WindowStyle = "Hidden"
    PassThru = $true
}
$process = Start-Process @startParameters
Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ascii -NoNewline

$deadline = [DateTime]::UtcNow.AddMinutes(8)
do {
    if ($process.HasExited) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        throw "Pure Agent local service exited during explicit startup"
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -TimeoutSec 2
        if ($health.status -eq "ok") { break }
    } catch {
        Start-Sleep -Seconds 1
    }
} while ([DateTime]::UtcNow -lt $deadline)
if ([DateTime]::UtcNow -ge $deadline) {
    $listenerPid = Get-LoopbackListenerProcessId -ListenerPort $Port
    if ($listenerPid -and $listenerPid -ne $process.Id) {
        Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    }
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw "Pure Agent local service did not become ready before the startup deadline"
}

$listenerPid = Get-LoopbackListenerProcessId -ListenerPort $Port
if (-not $listenerPid) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw "Pure Agent health succeeded without an owned loopback listener"
}
Set-Content -LiteralPath $pidFile -Value $listenerPid -Encoding ascii -NoNewline

Write-Host "Pure Agent local service is ready: http://127.0.0.1:$Port/admin/bid-assessment-pure-agent"
