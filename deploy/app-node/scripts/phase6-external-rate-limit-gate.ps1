$ErrorActionPreference = 'Stop'

$Domain = 'www.qskingship.com'
$Failures = [System.Collections.Generic.List[string]]::new()

function Add-Pass {
    param([string]$Message)
    Write-Output "PASS|$Message"
}

function Add-Fail {
    param([string]$Message)
    $script:Failures.Add($Message)
    Write-Output "FAIL|$Message"
}

function Invoke-ParallelStatusGate {
    param(
        [string[]]$Urls,
        [int]$ParallelMaximum
    )

    $Arguments = @(
        '--silent',
        '--show-error',
        '--noproxy',
        '*',
        '--write-out',
        "%{http_code}`n",
        '--max-time',
        '20',
        '--parallel',
        '--parallel-immediate',
        '--parallel-max',
        $ParallelMaximum.ToString()
    )
    foreach ($Url in $Urls) {
        $Arguments += @('--output', 'NUL', $Url)
    }

    $RawOutput = (& curl.exe @Arguments 2>$null | Out-String)
    return @(
        [regex]::Matches($RawOutput, '\d{3}') |
            ForEach-Object { $_.Value }
    )
}

Start-Sleep -Seconds 50

$LoginUrls = @(
    1..12 | ForEach-Object {
        "https://$Domain/api/v1/auth/login?phase6_login_rate_gate=$_"
    }
)
$LoginCodes = Invoke-ParallelStatusGate -Urls $LoginUrls -ParallelMaximum 12
$Login429 = @($LoginCodes | Where-Object { $_ -eq '429' }).Count
$Login405 = @($LoginCodes | Where-Object { $_ -eq '405' }).Count
if ($LoginCodes.Count -eq 12 -and $Login429 -gt 0 -and $Login405 -gt 0) {
    Add-Pass "login_rate_limit|responses_12|http_405_$Login405|http_429_$Login429"
}
else {
    Add-Fail "login_rate_limit|responses_$($LoginCodes.Count)|http_405_$Login405|http_429_$Login429"
}

$GeneralUrls = @(
    1..90 | ForEach-Object {
        "https://$Domain/favicon.ico?phase6_general_rate_gate=$_"
    }
)
$GeneralCodes = Invoke-ParallelStatusGate -Urls $GeneralUrls -ParallelMaximum 60
$General429 = @($GeneralCodes | Where-Object { $_ -eq '429' }).Count
$GeneralNon429 = @($GeneralCodes | Where-Object { $_ -ne '429' }).Count
if ($GeneralCodes.Count -eq 90 -and $General429 -gt 0 -and $GeneralNon429 -gt 0) {
    Add-Pass "general_rate_limit|responses_90|non_429_$GeneralNon429|http_429_$General429"
}
else {
    Add-Fail "general_rate_limit|responses_$($GeneralCodes.Count)|non_429_$GeneralNon429|http_429_$General429"
}

Start-Sleep -Seconds 5
$RecoveryCode = @(
    & curl.exe --silent --show-error --noproxy '*' --output NUL --write-out '%{http_code}' --max-time 20 "https://$Domain/" 2>$null
)[-1]
if ($RecoveryCode -in @('200', '301', '302', '303', '307', '308')) {
    Add-Pass "rate_limit_recovery|http_$RecoveryCode"
}
else {
    Add-Fail "rate_limit_recovery|http_$RecoveryCode"
}

if ($Failures.Count -gt 0) {
    Write-Output "RESULT=FAIL|count_$($Failures.Count)"
    exit 1
}

Write-Output 'RESULT=PASS|phase6_external_rate_limit_gate'
