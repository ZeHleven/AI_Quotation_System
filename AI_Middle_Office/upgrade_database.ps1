# AI Middle Office - database migration runner

[CmdletBinding()]
param(
    [string]$Revision = "head"
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$WorkDir = $PSScriptRoot
$AlembicIni = Join-Path $WorkDir "alembic.ini"

function Find-Python {
    $candidates = @(
        "C:\Users\12521\miniconda3\python.exe",
        "C:\Users\12521\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw "python.exe not found. Please install Miniconda or update upgrade_database.ps1."
}

if (-not (Test-Path $AlembicIni)) {
    throw "alembic.ini not found: $AlembicIni"
}

$PythonPath = Find-Python
Set-Location $WorkDir

Write-Host "[INFO] Running database migrations to revision: $Revision" -ForegroundColor Cyan
Write-Host "[INFO] Python: $PythonPath"

& $PythonPath -c "from alembic.config import main; main()" -c $AlembicIni upgrade $Revision
if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed with exit code $LASTEXITCODE. Run: pip install -r requirements.txt"
}

Write-Host "[OK] Database migrations completed" -ForegroundColor Green
