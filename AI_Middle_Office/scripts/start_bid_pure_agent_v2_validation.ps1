param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 9019,
    [string]$PythonExecutable = "",
    [string]$PdfPath = "",
    [string]$EmbeddingModelPath = "",
    [string]$SecretEnvFile = "C:\Users\12521\.secrets\bid-agent.env",
    [switch]$InitializeLocalDatabase,
    [switch]$PreflightOnly,
    [switch]$ReplaceLocalInstance
)

$ErrorActionPreference = "Stop"
$entryScript = Join-Path $PSScriptRoot "start_bid_pure_agent_local.ps1"
if (-not (Test-Path -LiteralPath $entryScript -PathType Leaf)) {
    throw "The shared isolated Pure Agent entry is unavailable"
}

$parameters = @{
    Port = $Port
    DataDirectoryName = ".local-pure-agent-daily-v2-validation"
    SecretEnvFile = $SecretEnvFile
    InitializeLocalDatabase = $InitializeLocalDatabase.IsPresent
    PreflightOnly = $PreflightOnly.IsPresent
    ReplaceLocalInstance = $ReplaceLocalInstance.IsPresent
    EnableProviderBoundaryV2 = $true
}
if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $parameters.PythonExecutable = $PythonExecutable
}
if (-not [string]::IsNullOrWhiteSpace($PdfPath)) {
    $parameters.PdfPath = $PdfPath
}
if (-not [string]::IsNullOrWhiteSpace($EmbeddingModelPath)) {
    $parameters.EmbeddingModelPath = $EmbeddingModelPath
}

& $entryScript @parameters
if ($PreflightOnly) { return }

Write-Host "V2 validation login: http://127.0.0.1:$Port/login"
Write-Host (
    "V2 validation workspace: " +
    "http://127.0.0.1:$Port/admin/bid-assessment-pure-agent"
)
Write-Host "Isolation data: .local-pure-agent-daily-v2-validation"
