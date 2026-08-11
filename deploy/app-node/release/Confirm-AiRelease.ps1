[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^rel-[0-9]{8}-[0-9]{6}-[0-9a-f]{7,12}$')]
    [string]$ReleaseId
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$releaseDirectory = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $releaseDirectory "..\..\..")).Path
$manifestPath = Join-Path $repositoryRoot ".tmp\release-bundles\$ReleaseId\release-manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Local release manifest not found: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.release_id -ne $ReleaseId -or $manifest.schema_version -ne 1) {
    throw "Release manifest identity mismatch."
}

$commonGitDirectory = @(& git -C $repositoryRoot rev-parse --git-common-dir)
if ($LASTEXITCODE -ne 0 -or $commonGitDirectory.Count -ne 1) {
    throw "Unable to resolve the Git common directory."
}
$commonGitDirectory = ([string]$commonGitDirectory[0]).Trim()
if (-not [System.IO.Path]::IsPathRooted($commonGitDirectory)) {
    $commonGitDirectory = (Resolve-Path -LiteralPath (Join-Path $repositoryRoot $commonGitDirectory)).Path
}
$targetPath = Join-Path $commonGitDirectory "ai-release-production-baseline.json"

$baseline = [ordered]@{
    schema_version = 1
    production_commit = [string]$manifest.source.commit
    image_tag = [string]$manifest.image.tag
    image_id = [string]$manifest.image.id
    database_head = [string]$manifest.database.target_head
    deployed_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    agent_runtime_deployed = [bool]$manifest.gates.agent_runtime_allowed
    release_id = $ReleaseId
}
$baseline | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $targetPath -Encoding UTF8
Write-Host "PASS|local_production_baseline_updated=$targetPath|commit=$($baseline.production_commit)|release_id=$ReleaseId"
