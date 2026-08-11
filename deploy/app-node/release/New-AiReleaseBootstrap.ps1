[CmdletBinding()]
param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$managerPath = Join-Path $PSScriptRoot "ecs-ai-release"
if (-not (Test-Path -LiteralPath $managerPath -PathType Leaf)) {
    throw "ECS release manager not found: $managerPath"
}

$managerBytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $managerPath).Path)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $managerHash = ([System.BitConverter]::ToString($sha256.ComputeHash($managerBytes))).Replace("-", "").ToLowerInvariant()
}
finally {
    $sha256.Dispose()
}

$compressedStream = New-Object System.IO.MemoryStream
try {
    $gzip = New-Object System.IO.Compression.GzipStream(
        $compressedStream,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true
    )
    try {
        $gzip.Write($managerBytes, 0, $managerBytes.Length)
    }
    finally {
        $gzip.Dispose()
    }
    $payload = [Convert]::ToBase64String($compressedStream.ToArray())
}
finally {
    $compressedStream.Dispose()
}

$temporaryPath = "/tmp/ai-release.bootstrap"
$command = "printf '%s' '$payload' | base64 -d | gzip -d > '$temporaryPath' && printf '%s  %s\n' '$managerHash' '$temporaryPath' | sha256sum -c - && sudo install -o root -g root -m 0700 '$temporaryPath' /usr/local/sbin/ai-release && rm -f -- '$temporaryPath' && sudo /usr/local/sbin/ai-release status"
if ([Text.Encoding]::UTF8.GetByteCount($command) -gt 30000) {
    throw "Generated SSM bootstrap command is too large."
}

if (-not $OutputPath) {
    $repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
    $outputDirectory = Join-Path $repositoryRoot ".tmp\release-bootstrap"
    [void](New-Item -ItemType Directory -Path $outputDirectory -Force)
    $OutputPath = Join-Path $outputDirectory "install-ai-release-ssm-command.txt"
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.File]::WriteAllText($resolvedOutput, $command + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "PASS|ssm_bootstrap_command=$resolvedOutput|manager_sha256=$managerHash|command_bytes=$([Text.Encoding]::UTF8.GetByteCount($command))"
Write-Host "NEXT|Copy the one line from that file into Alibaba Cloud SSM/Cloud Assistant."
