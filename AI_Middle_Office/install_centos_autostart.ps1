# Upload and run the CentOS autostart helper.
# This script may prompt for the SSH key passphrase.

[CmdletBinding()]
param(
    [string]$CentosHost = "192.168.88.128",
    [string]$User = "root",
    [string]$Interface = "ens33",
    [string]$RemoteDir = "/opt/rag_service"
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$SourceScript = Resolve-Path (Join-Path $RepoRoot "rag_docker\enable_centos_autostart.sh")
$RemoteScript = "${RemoteDir}/enable_centos_autostart.sh"
$Target = "${User}@${CentosHost}:${RemoteScript}"

Write-Host "[INFO] Uploading $SourceScript to $Target" -ForegroundColor Cyan
scp "$SourceScript" "$Target"

Write-Host "[INFO] Running CentOS autostart setup on ${CentosHost}" -ForegroundColor Cyan
ssh "${User}@${CentosHost}" "cd ${RemoteDir} && sudo bash ${RemoteScript} ${Interface}"

Write-Host "[OK] CentOS autostart setup completed" -ForegroundColor Green
