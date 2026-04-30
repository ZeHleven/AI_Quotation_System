# AI Middle Office startup acceptance check.
# Run after reboot or after start_all.ps1 finishes.

[CmdletBinding()]
param(
    [string]$AppUrl = "http://localhost:9000",
    [string]$CentosHost = "",
    [int]$TimeoutSeconds = 5,
    [switch]$RequireMinio
)

$ErrorActionPreference = "Stop"
$WorkDir = $PSScriptRoot
$EnvFile = Join-Path $WorkDir ".env"

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

function Test-TcpPort {
    param(
        [string]$Name,
        [string]$TargetHost,
        [int]$Port
    )
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne($TimeoutSeconds * 1000, $false)
        if (-not $ok) {
            $client.Close()
            throw "timeout"
        }
        $client.EndConnect($async)
        $client.Close()
        Write-Host "[OK] $Name ${TargetHost}:$Port" -ForegroundColor Green
        return $true
    } catch {
        if ($client) { $client.Close() }
        Write-Host "[FAIL] $Name ${TargetHost}:$Port - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Test-HttpGet {
    param(
        [string]$Name,
        [string]$Url
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSeconds -UseBasicParsing
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            Write-Host "[OK] $Name $Url ($($response.StatusCode))" -ForegroundColor Green
            return $true
        }
        Write-Host "[FAIL] $Name $Url ($($response.StatusCode))" -ForegroundColor Red
        return $false
    } catch {
        Write-Host "[FAIL] $Name $Url - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Test-RagRetrieve {
    param([string]$RagUrl)
    try {
        # Keep this query ASCII so Windows PowerShell 5 can run the script even
        # when it reads UTF-8 files without a BOM.
        $body = @{ query = "PPR"; top_k = 1 } | ConvertTo-Json -Compress
        $response = Invoke-RestMethod -Uri "$($RagUrl.TrimEnd('/'))/api/v1/retrieve" -Method Post -Body $body -ContentType "application/json" -TimeoutSec $TimeoutSeconds
        $count = 0
        if ($response.data) { $count = @($response.data).Count }
        if ($count -gt 0) {
            Write-Host "[OK] RAG retrieve returned $count item(s)" -ForegroundColor Green
            return $true
        }
        Write-Host "[FAIL] RAG retrieve returned no items" -ForegroundColor Red
        return $false
    } catch {
        Write-Host "[FAIL] RAG retrieve - $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

if (-not $CentosHost) {
    if ($env:MIDDLE_OFFICE_CENTOS_HOST) {
        $CentosHost = $env:MIDDLE_OFFICE_CENTOS_HOST
    } else {
        $CentosHost = "192.168.88.128"
    }
}

$ragUrl = Get-DotEnvValue -Name "RAG_SERVICE_URL" -Default "http://$CentosHost:8001"
$minioEnabled = (Get-DotEnvValue -Name "MINIO_ENABLED" -Default "false").ToLowerInvariant()
$shouldCheckMinio = $RequireMinio -or ($minioEnabled -in @("1", "true", "yes", "on"))

$checks = [ordered]@{}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " AI Middle Office acceptance check"
Write-Host " App:    $AppUrl"
Write-Host " CentOS: $CentosHost"
Write-Host " RAG:    $ragUrl"
Write-Host "========================================" -ForegroundColor Cyan

$checks["mysql_port"] = Test-TcpPort -Name "MySQL" -TargetHost $CentosHost -Port 5455
$checks["redis_port"] = Test-TcpPort -Name "Redis" -TargetHost $CentosHost -Port 6380
$checks["rag_port"] = Test-TcpPort -Name "RAG service" -TargetHost $CentosHost -Port 8001
$checks["n8n_port"] = Test-TcpPort -Name "n8n" -TargetHost $CentosHost -Port 5678
if ($shouldCheckMinio) {
    $checks["minio_port"] = Test-TcpPort -Name "MinIO" -TargetHost $CentosHost -Port 9002
    $checks["minio_health"] = Test-HttpGet -Name "MinIO health" -Url "http://$CentosHost:9002/minio/health/live"
} else {
    Write-Host "[SKIP] MinIO acceptance skipped because MINIO_ENABLED is false" -ForegroundColor Yellow
}

$checks["rag_docs"] = Test-HttpGet -Name "RAG docs" -Url "$($ragUrl.TrimEnd('/'))/docs"
$checks["rag_retrieve"] = Test-RagRetrieve -RagUrl $ragUrl

try {
    $ready = Invoke-RestMethod -Uri "$($AppUrl.TrimEnd('/'))/health/ready" -TimeoutSec $TimeoutSeconds
    $queueOk = $false
    if ($ready.task_queue -and $ready.task_queue.ok -eq $true) { $queueOk = $true }
    $readyOk = ($ready.status -eq "ready") -and ($ready.database -eq "ok") -and $queueOk
    if ($readyOk) {
        Write-Host "[OK] FastAPI ready, database ok, queue ok" -ForegroundColor Green
    } else {
        Write-Host "[FAIL] FastAPI readiness is degraded" -ForegroundColor Red
        $ready | ConvertTo-Json -Depth 8
    }
    $checks["fastapi_ready"] = $readyOk
} catch {
    Write-Host "[FAIL] FastAPI /health/ready - $($_.Exception.Message)" -ForegroundColor Red
    $checks["fastapi_ready"] = $false
}

$failed = @($checks.GetEnumerator() | Where-Object { -not $_.Value })
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "[PASS] Acceptance check passed" -ForegroundColor Green
    exit 0
}

Write-Host "[FAIL] Acceptance check failed: $($failed.Name -join ', ')" -ForegroundColor Red
exit 1
