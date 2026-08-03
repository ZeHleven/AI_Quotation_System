#Requires -Version 5.1

param(
    [string]$BackupRoot      = "C:\AI_Backups",
    [string]$CentosHost      = "192.168.88.128",
    [int]   $MysqlPort       = 5455,
    [string]$MysqlContainer  = "ragflow-mysql-1",
    [string]$MysqlUser       = "ai_app",
    [string]$MysqlPassword   = "",
    [string]$MysqlDatabase   = "ai_quotation",
    [int]   $RetainDays      = 7,
    [switch]$LegacyDirectBackup
)

$ErrorActionPreference = "Stop"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dest      = Join-Path $BackupRoot $timestamp
$scriptDir = $PSScriptRoot

if (-not $LegacyDirectBackup) {
    Write-Warning "backup_all.ps1 is a legacy direct Windows backup helper."
    Write-Warning "For trial and production-like backups, use run_centos_backup.ps1 instead."
    Write-Warning "Re-run with -LegacyDirectBackup only for emergency legacy direct backups."
    exit 2
}

if (-not $MysqlPassword) {
    $MysqlPassword = [Environment]::GetEnvironmentVariable("MIDDLE_OFFICE_MYSQL_PASSWORD", "Process")
}
if (-not $MysqlPassword) {
    throw "MysqlPassword is required. Pass -MysqlPassword or set MIDDLE_OFFICE_MYSQL_PASSWORD for this process."
}

function Write-Log {
    param([string]$Msg)
    $line = "[$timestamp] $Msg"
    Write-Output $line
    Add-Content -Path (Join-Path $BackupRoot "backup.log") -Value $line -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dest "db")  | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dest "rag") | Out-Null

Write-Log "=== Backup started -> $dest ==="

# 1. MySQL dump via pymysql (direct connection to port 5455, no SSH needed)
Write-Log "1/3 MySQL dump..."
$dumpFile  = Join-Path $dest "db\ai_quotation.sql"
$pyScript  = Join-Path $scriptDir "backup_mysql.py"
$python    = "C:\Users\12521\miniconda3\python.exe"
try {
    & $python $pyScript $CentosHost $MysqlPort $MysqlUser $MysqlPassword $MysqlDatabase $dumpFile
    if ($LASTEXITCODE -ne 0) { throw "backup_mysql.py exited $LASTEXITCODE" }
    $sizeMB = [math]::Round((Get-Item $dumpFile).Length / 1MB, 2)
    Write-Log "   MySQL dump OK, size ${sizeMB} MB"
} catch {
    Write-Log "   [WARN] MySQL dump failed: $_ (skipping, continuing)"
}

# 2. RAG knowledge base files
Write-Log "2/3 RAG knowledge base..."
$ragSrc         = Split-Path $scriptDir -Parent
$materialsJson  = Join-Path $ragSrc "rag_materials.json"
$materialsAudit = Join-Path $ragSrc "materials_audit"
try {
    if (Test-Path $materialsJson) {
        Copy-Item $materialsJson (Join-Path $dest "rag\rag_materials.json") -Force
    }
    if (Test-Path $materialsAudit) {
        robocopy $materialsAudit (Join-Path $dest "rag\materials_audit") /E /NP /NFL /NDL /NJH /NJS | Out-Null
    }
    Write-Log "   RAG backup OK"
} catch {
    Write-Log "   [WARN] RAG backup failed: $_"
}

# 3. Purge backups older than RetainDays
Write-Log "3/3 Purging backups older than $RetainDays days..."
$cutoff = (Get-Date).AddDays(-$RetainDays)
Get-ChildItem $BackupRoot -Directory |
    Where-Object { $_.Name -match '^\d{8}_\d{6}$' -and $_.CreationTime -lt $cutoff } |
    ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force
        Write-Log "   Deleted old backup: $($_.Name)"
    }

Write-Log "=== Backup complete -> $dest ==="
Write-Output ""
Write-Output "Verify:"
Write-Output "  Backup dir : $dest"
Write-Output "  MySQL dump : $dumpFile"
