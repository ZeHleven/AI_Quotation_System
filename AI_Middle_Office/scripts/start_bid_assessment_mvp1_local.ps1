param(
    [int]$Port = 9001,
    [string]$PythonExecutable = "python",
    [ValidateSet("deterministic", "deepseek-v4-flash")]
    [string]$ModelMode = "deterministic",
    [ValidateSet("legacy", "rq2b", "rq2c")]
    [string]$RetrievalMode = "legacy",
    [ValidateSet("view-only")]
    [string]$AccessMode = "view-only",
    [string]$LabDirectoryName = "",
    [string]$EmbeddingModelPath = "C:\Users\12521\.cache\huggingface\hub\models--maidalun1020--bce-embedding-base_v1\snapshots\9c0d82af44af61abe171ffae23fde5740c0ec1a8",
    [string]$RerankerModelPath = "C:\Users\12521\.cache\huggingface\hub\models--maidalun1020--bce-reranker-base_v1\snapshots\eb7650fca1d81e2856fbd0d522488844aa502735",
    [string]$SecretEnvFile = "",
    [switch]$EnableMvpReleaseCandidate,
    [switch]$EnableBusinessBaseline,
    [switch]$EnableEnterpriseEvidenceImport,
    [switch]$EnableFactVerification,
    [switch]$ReplaceLocalPreview
)

$ErrorActionPreference = "Stop"
if ($EnableMvpReleaseCandidate -and $AccessMode -ne "execute") {
    throw "EnableMvpReleaseCandidate requires AccessMode=execute"
}
if ($EnableBusinessBaseline -and $AccessMode -ne "execute") {
    throw "EnableBusinessBaseline requires AccessMode=execute"
}
if ($EnableEnterpriseEvidenceImport -and $AccessMode -ne "execute") {
    throw "EnableEnterpriseEvidenceImport requires AccessMode=execute"
}
if ($EnableFactVerification -and $AccessMode -ne "execute") {
    throw "EnableFactVerification requires AccessMode=execute"
}
$middleOfficeDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryDir = (Resolve-Path (Join-Path $middleOfficeDir "..")).Path
$historicalLabDirectoryName = if ($RetrievalMode -ne "legacy") {
    ".local-mvp1-real-$RetrievalMode"
} elseif ($ModelMode -eq "deepseek-v4-flash") {
    ".local-mvp1-ds-b2"
} else {
    ".local-mvp1"
}
$defaultLabDirectoryName = if ($AccessMode -eq "execute") {
    if ($EnableFactVerification) {
        "$historicalLabDirectoryName-phase4d3"
    } elseif ($EnableEnterpriseEvidenceImport) {
        "$historicalLabDirectoryName-phase4d2"
    } elseif ($EnableBusinessBaseline) {
        "$historicalLabDirectoryName-phase4d1"
    } elseif ($EnableMvpReleaseCandidate) {
        "$historicalLabDirectoryName-phase4c3"
    } else {
        "$historicalLabDirectoryName-phase4c1"
    }
} else {
    $historicalLabDirectoryName
}
$labDirectoryName = if ($LabDirectoryName) { $LabDirectoryName.Trim() } else { $defaultLabDirectoryName }
$phase4d3Lab = $EnableFactVerification -or $labDirectoryName -match '-phase4d3(?:-|$)'
$phase4d2Lab = $EnableEnterpriseEvidenceImport -or $phase4d3Lab -or $labDirectoryName -match '-phase4d2(?:-|$)'
$phase4cLab = $AccessMode -eq "execute" -or $labDirectoryName -match '-phase4c(?:1|3)(?:-|$)' -or $labDirectoryName -match '-phase4d(?:1|2|3)(?:-|$)'
$phase4c3Lab = $EnableMvpReleaseCandidate -or $EnableBusinessBaseline -or $phase4d2Lab -or $labDirectoryName -match '-phase4c3(?:-|$)' -or $labDirectoryName -match '-phase4d(?:1|2|3)(?:-|$)'
$phase4d1Lab = $EnableBusinessBaseline -or $phase4d2Lab -or $labDirectoryName -match '-phase4d(?:1|2|3)(?:-|$)'
if ($labDirectoryName -notmatch '^\.local-mvp1(?:-[a-z0-9][a-z0-9-]{0,47})?$') {
    throw "LabDirectoryName must be a local .local-mvp1[-name] directory"
}
$labDir = Join-Path $middleOfficeDir $labDirectoryName
$databasePath = Join-Path $labDir "runtime.db"
$storageRoot = Join-Path $labDir "objects"
$logDir = Join-Path $middleOfficeDir "logs"
$logStem = if ($LabDirectoryName -or $AccessMode -eq "execute") {
    "bid-" + $labDirectoryName.TrimStart(".")
} elseif ($RetrievalMode -ne "legacy") {
    "bid-mvp1-real"
} else {
    "bid-mvp1-local"
}
$stdoutLog = Join-Path $logDir "$logStem.out.log"
$stderrLog = Join-Path $logDir "$logStem.err.log"
$pidFile = Join-Path $logDir "$logStem.pid"
$distIndex = Join-Path $repositoryDir "ai-web\dist\index.html"
$localDependencyPath = Join-Path $middleOfficeDir ".tmp\phase4b1-test-deps"
$rq2DependencyPath = Join-Path $middleOfficeDir ".tmp\rq2-locked-runtime"

if (-not (Test-Path -LiteralPath $distIndex)) {
    throw "ai-web/dist/index.html is missing; build the Vite frontend first"
}
if ($AccessMode -eq "view-only" -and -not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
    throw "View-only mode requires an existing isolated database: $databasePath"
}
if ($AccessMode -eq "execute" -and $RetrievalMode -ne "legacy" -and -not (Test-Path -LiteralPath $EmbeddingModelPath -PathType Container)) {
    throw "Frozen local BCE embedding snapshot is missing: $EmbeddingModelPath"
}
if ($AccessMode -eq "execute" -and $RetrievalMode -eq "rq2c" -and -not (Test-Path -LiteralPath $RerankerModelPath -PathType Container)) {
    throw "Frozen local BCE reranker snapshot is missing: $RerankerModelPath"
}
if (Test-Path -LiteralPath $localDependencyPath -PathType Container) {
    $existingPythonPath = [System.Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    $env:PYTHONPATH = if ($existingPythonPath) {
        "$localDependencyPath;$existingPythonPath"
    } else {
        $localDependencyPath
    }
}
if ($RetrievalMode -ne "legacy") {
    if (-not (Test-Path -LiteralPath $rq2DependencyPath -PathType Container)) {
        throw "Frozen RQ2 Worker dependencies are missing: $rq2DependencyPath"
    }
    $env:PYTHONPATH = "$rq2DependencyPath;$env:PYTHONPATH"
}
if ($AccessMode -eq "view-only" -and $SecretEnvFile) {
    throw "SecretEnvFile is not accepted in view-only mode"
}
if ($AccessMode -eq "view-only") {
    $env:BID_ASSESSMENT_MODEL_API_KEY = "local-view-only-disabled"
    $env:DEEPSEEK_API_KEY = "local-view-only-disabled"
}
if ($AccessMode -eq "execute" -and $SecretEnvFile) {
    if (-not (Test-Path -LiteralPath $SecretEnvFile -PathType Leaf)) {
        throw "Secret env file does not exist: $SecretEnvFile"
    }
    foreach ($line in Get-Content -LiteralPath $SecretEnvFile) {
        if ($line -match '^\s*(BID_ASSESSMENT_MODEL_API_KEY|DEEPSEEK_API_KEY)\s*=\s*(.*)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            if ($value) { Set-Item -Path "Env:$name" -Value $value }
        }
    }
}
if ($AccessMode -eq "execute" -and $ModelMode -eq "deepseek-v4-flash") {
    $modelApiKey = [System.Environment]::GetEnvironmentVariable("BID_ASSESSMENT_MODEL_API_KEY", "Process")
    if ([string]::IsNullOrWhiteSpace($modelApiKey)) {
        $modelApiKey = [System.Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "Process")
    }
    if (
        [string]::IsNullOrWhiteSpace($modelApiKey) -or
        $modelApiKey.Trim().Length -lt 20 -or
        $modelApiKey -match '(?i)^(change[-_ ]?me|example|placeholder|local-view-only|dummy|test)'
    ) {
        throw "Execute/deepseek-v4-flash mode requires a non-placeholder model API key"
    }
}

function Get-LocalListenerProcessId([int]$ListenerPort) {
    $pattern = "^\s*TCP\s+127\.0\.0\.1:$ListenerPort\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match $pattern) { return [int]$Matches[1] }
    }
    return $null
}

$existingPid = Get-LocalListenerProcessId -ListenerPort $Port
if ($existingPid) {
    $knownPidFiles = @(
        $pidFile,
        (Join-Path $logDir "bid-mvp0-preview.pid"),
        (Join-Path $logDir "bid-mvp1-local.pid"),
        (Join-Path $logDir "bid-mvp1-real.pid")
    )
    $replaceable = $false
    foreach ($knownPidFile in $knownPidFiles) {
        if (Test-Path -LiteralPath $knownPidFile) {
            $knownPid = [int](Get-Content -LiteralPath $knownPidFile -Raw).Trim()
            if ($knownPid -eq $existingPid) { $replaceable = $true }
        }
    }
    if (-not $ReplaceLocalPreview -or -not $replaceable) {
        throw "127.0.0.1:$Port is already in use by PID $existingPid"
    }
    Stop-Process -Id $existingPid -Force
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
if ($AccessMode -eq "execute") {
    New-Item -ItemType Directory -Path $labDir, $storageRoot -Force | Out-Null
}
$databaseUrl = "sqlite:///" + ($databasePath -replace "\\", "/")
$processPath = [System.Environment]::GetEnvironmentVariable("Path", "Process")
Remove-Item Env:Path -ErrorAction SilentlyContinue
Remove-Item Env:PATH -ErrorAction SilentlyContinue
$env:Path = $processPath
$env:APP_ENV = "local"
$env:DATABASE_URL = $databaseUrl
$env:TASK_QUEUE_MODE = "local"
$env:AUTO_CREATE_TABLES = "false"
$env:STARTUP_COMPAT_MIGRATIONS = "false"
$env:PUBLIC_ACCESS_ENABLED = "false"
$env:BID_MVP1_LOCAL_LAB = "1"
$env:BID_MVP1_LOCAL_ACCESS_MODE = $AccessMode
$env:BID_MVP1_LOCAL_MAX_INPUT_TOKENS = if ($RetrievalMode -ne "legacy") { "64000" } else { "" }
$env:BID_MVP1_LOCAL_BIND_HOST = "127.0.0.1"
$env:BID_MVP1_LOCAL_MODEL_MODE = $ModelMode
$env:BID_MVP1_LOCAL_RETRIEVAL_MODE = $RetrievalMode
$env:BID_UPLOAD_STORAGE_BACKEND = "local"
$env:BID_UPLOAD_LOCAL_ROOT = $storageRoot
$env:BID_TOOL_SCOPE_SIGNING_KEY = "mvp1-local-only-scope-key-20260814"
$env:FEATURE_BID_ASSESSMENT_V1_RUNTIME = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE2_DOCUMENT_WORKER = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE2_LOT_WORKER = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_COMPLETE_RUNTIME = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_RUN_BOOTSTRAP = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_PLANNER = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_TASK_RUNTIME = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_RUN_LIFECYCLE = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_TOOL_CONTEXT = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_TOOL_EXECUTOR = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE3_RUN_VALIDATION = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_MVP = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_PLAN_CONTINUATION = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_LOCAL_AGENT = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_EVIDENCE_MCP = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_MODEL_EXECUTOR = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_DEEPSEEK_ADAPTER = if ($ModelMode -eq "deepseek-v4-flash") { "true" } else { "false" }
$env:FEATURE_BID_ASSESSMENT_PHASE4_FACT_AUTHORITY = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_PRELIMINARY_REPORT = "true"
$env:FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_CAPABILITY = if ($phase4cLab) { "true" } else { "false" }
$env:FEATURE_BID_ASSESSMENT_PHASE4_MVP_RELEASE_CANDIDATE = if ($phase4c3Lab) { "true" } else { "false" }
$env:FEATURE_BID_ASSESSMENT_PHASE4_BUSINESS_BASELINE = if ($phase4d1Lab) { "true" } else { "false" }
$env:FEATURE_BID_ASSESSMENT_PHASE4_ENTERPRISE_EVIDENCE_IMPORT = if ($phase4d2Lab) { "true" } else { "false" }
$env:FEATURE_BID_ASSESSMENT_PHASE4_FACT_VERIFICATION = if ($phase4d3Lab) { "true" } else { "false" }
$env:FEATURE_BID_ASSESSMENT_PHASE4_MVP0_TRACE = "true"
if ($RetrievalMode -ne "legacy") {
    $env:FEATURE_BID_ASSESSMENT_PDF_C2_NATIVE_LAYOUT = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ1A_STRUCTURE_AGGREGATION = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ1B_PARSE_QUALITY_GATE = "true"
    $env:FEATURE_BID_ASSESSMENT_PDF_C3_ROLE_AWARE_RETRIEVAL = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ1C_QUERY_OPTIMIZER = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ1D_FIELD_AWARE_LEXICAL = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ2A_SEMANTIC_RECALL = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ2B_CANDIDATE_FUSION = "true"
    $env:FEATURE_BID_ASSESSMENT_RQ2C_LIGHTWEIGHT_RERANK = if ($RetrievalMode -eq "rq2c") { "true" } else { "false" }
    $env:BID_DOCUMENT_PARSER_PROFILE_VERSION = "bid-document-parser-profile-v4-pdf-quality-gated-rq1b"
    $env:BID_EVIDENCE_RETRIEVAL_PROFILE_VERSION = "bid-evidence-retrieval-profile-v2-role-aware"
    $env:BID_EVIDENCE_QUERY_OPTIMIZER_PROFILE_VERSION = "bid-evidence-query-optimizer-profile-v1-rq1c"
    $env:BID_EVIDENCE_LEXICAL_SEARCH_PROFILE_VERSION = "bid-evidence-lexical-profile-v1-rq1d"
    $env:BID_EVIDENCE_SEMANTIC_SEARCH_PROFILE_VERSION = "bid-evidence-semantic-profile-v1-rq2a-bce"
    $env:BID_EVIDENCE_CANDIDATE_FUSION_PROFILE_VERSION = "bid-evidence-candidate-fusion-profile-v1-rq2b"
    $env:BID_EVIDENCE_RERANK_PROFILE_VERSION = if ($RetrievalMode -eq "rq2c") { "bid-evidence-rerank-profile-v1-rq2c-bce" } else { "bid-evidence-rerank-profile-v0-disabled" }
    $env:BID_EVIDENCE_SEMANTIC_PROVIDER_ID = "bce-milvus"
    $env:BID_EVIDENCE_SEMANTIC_MODEL_PATH = $EmbeddingModelPath
    $env:BID_EVIDENCE_SEMANTIC_MODEL_OFFLINE = "true"
    $env:BID_EVIDENCE_SEMANTIC_MILVUS_HOST = "127.0.0.1"
    $env:BID_MVP1_LOCAL_SEMANTIC_BACKEND = "exact-cosine"
    $env:BID_EVIDENCE_RERANK_PROVIDER_ID = if ($RetrievalMode -eq "rq2c") { "bce-cross-encoder-local" } else { "disabled" }
    $env:BID_EVIDENCE_RERANK_MODEL_PATH = if ($RetrievalMode -eq "rq2c") { $RerankerModelPath } else { "" }
    $env:BID_EVIDENCE_RERANK_OFFLINE = "true"
}
if ($ModelMode -eq "deepseek-v4-flash") {
    $env:BID_ASSESSMENT_MODEL_PROVIDER_REF = "deepseek"
    $env:BID_ASSESSMENT_MODEL_ID = "deepseek-v4-flash"
    $env:BID_ASSESSMENT_MODEL_CHAT_URL = "https://api.deepseek.com/chat/completions"
    $env:BID_ASSESSMENT_MODEL_THINKING_MODE = "disabled"
}
$env:UVICORN_LIFESPAN = "auto"

$arguments = @(
    "-m", "uvicorn", "app.mvp1_local:app",
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
for ($attempt = 0; $attempt -lt 80; $attempt += 1) {
    Start-Sleep -Milliseconds 125
    $listenerPid = Get-LocalListenerProcessId -ListenerPort $Port
    if ($listenerPid) { break }
    if ($process.HasExited) { break }
}
if (-not $listenerPid) {
    throw "MVP-1 local lab did not start; inspect $stderrLog"
}
$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/live" -TimeoutSec 5
if ($health.status -ne "ok" -or $health.access_mode -ne $AccessMode) {
    Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    throw "MVP-1 local lab health/access-mode verification failed"
}
$capabilityEnvelope = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/bid-assessment-runtime-lab/capabilities" -TimeoutSec 5
$capabilities = $capabilityEnvelope.data
if (
    $null -eq $capabilities -or
    $capabilities.access_mode -ne $AccessMode -or
    [bool]$capabilities.write_enabled -ne ($AccessMode -eq "execute") -or
    [bool]$capabilities.worker_running -ne ($AccessMode -eq "execute")
) {
    Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    throw "MVP-1 local lab capability verification failed"
}
Set-Content -LiteralPath $pidFile -Value $listenerPid -Encoding ascii
Write-Output "MVP-1 isolated local lab started in $AccessMode/$ModelMode/$RetrievalMode mode: http://127.0.0.1:$Port/admin/bid-assessment-runtime-lab (PID $listenerPid)"
