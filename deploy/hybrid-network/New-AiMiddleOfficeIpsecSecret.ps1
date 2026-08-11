[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

$parentDirectory = Split-Path -Parent $OutputPath
if (-not $parentDirectory -or -not (Test-Path -LiteralPath $parentDirectory -PathType Container)) {
    throw "The output directory does not exist: $parentDirectory"
}

if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite an existing secret file: $OutputPath"
}

$secretBytes = New-Object byte[] 64
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $random.GetBytes($secretBytes)
}
finally {
    $random.Dispose()
}

$secret = [Convert]::ToBase64String($secretBytes)
$secretLine = '@ecs-ai-middle-office @local-centos-rag : PSK "' + $secret + '"' + "`n"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($OutputPath, $secretLine, $utf8WithoutBom)

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $OutputPath /inheritance:r /grant:r "${currentIdentity}:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $OutputPath -Force
    throw "Failed to restrict the secret file ACL"
}

$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $OutputPath
[PSCustomObject]@{
    Path = $hash.Path
    SHA256 = $hash.Hash
}
