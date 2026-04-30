# Upload and run the CentOS production backup helper.
# This script runs remote SSH commands only when you execute it explicitly.

[CmdletBinding()]
param(
    [string]$CentosHost = "192.168.88.128",
    [string]$SshUser = "root",
    [string]$RemoteDir = "/opt/rag_service",
    [switch]$SkipUpload,
    [switch]$ColdMilvusSnapshot
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LocalScript = Join-Path $RepoRoot "rag_docker\backup_production.sh"

if (-not (Test-Path $LocalScript)) {
    throw "Backup script not found: $LocalScript"
}

$Target = "$SshUser@$CentosHost"
$RemoteScript = "$RemoteDir/backup_production.sh"

if (-not $SkipUpload) {
    Write-Host "[INFO] Uploading backup script to ${Target}:$RemoteScript" -ForegroundColor Cyan
    scp $LocalScript "${Target}:$RemoteScript"
    if ($LASTEXITCODE -ne 0) { throw "scp failed" }
}

$cold = if ($ColdMilvusSnapshot) { "true" } else { "false" }
$remoteCommand = "chmod +x '$RemoteScript' && cd '$RemoteDir' && STOP_MILVUS_FOR_BACKUP=$cold bash '$RemoteScript'"

Write-Host "[INFO] Running backup on $Target" -ForegroundColor Cyan
ssh $Target $remoteCommand
if ($LASTEXITCODE -ne 0) { throw "remote backup failed" }

Write-Host "[OK] Remote backup finished" -ForegroundColor Green
