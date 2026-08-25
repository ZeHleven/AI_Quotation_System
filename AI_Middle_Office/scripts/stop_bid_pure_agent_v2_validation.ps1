param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 9019
)

$ErrorActionPreference = "Stop"
$entryScript = Join-Path $PSScriptRoot "stop_bid_pure_agent_local.ps1"
if (-not (Test-Path -LiteralPath $entryScript -PathType Leaf)) {
    throw "The shared isolated Pure Agent stop entry is unavailable"
}

$parameters = @{
    DataDirectoryName = ".local-pure-agent-daily-v2-validation"
    Port = $Port
}
& $entryScript @parameters
