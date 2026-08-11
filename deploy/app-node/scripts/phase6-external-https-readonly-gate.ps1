$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$Domain = 'www.qskingship.com'
$ExpectedIp = '8.163.58.211'
$DnsServers = @('223.5.5.5', '1.1.1.1', '8.8.8.8')
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

foreach ($Server in $DnsServers) {
    try {
        $Addresses = @(
            Resolve-DnsName -Name $Domain -Type A -Server $Server -DnsOnly |
                Where-Object { $_.Type -eq 'A' } |
                Select-Object -ExpandProperty IPAddress -Unique
        )
        if ($Addresses.Count -eq 1 -and $Addresses[0] -eq $ExpectedIp) {
            Add-Pass "dns_a|resolver_$Server|$ExpectedIp"
        }
        else {
            Add-Fail "dns_a|resolver_$Server|unexpected_answer_count_$($Addresses.Count)"
        }
    }
    catch {
        Add-Fail "dns_a|resolver_$Server|lookup_error"
    }
}

$Tcp = $null
$Ssl = $null
try {
    $Tcp = [System.Net.Sockets.TcpClient]::new()
    $Connect = $Tcp.ConnectAsync($Domain, 443)
    if (-not $Connect.Wait(15000)) {
        throw 'TLS connect timeout'
    }
    $Ssl = [System.Net.Security.SslStream]::new($Tcp.GetStream(), $false)
    $Ssl.AuthenticateAsClient($Domain)
    $Certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $Ssl.RemoteCertificate
    )
    if ($Certificate.NotAfter.ToUniversalTime() -le [DateTime]::UtcNow.AddDays(30)) {
        Add-Fail 'tls_certificate|expires_within_30_days'
    }
    else {
        Add-Pass "tls_certificate|protocol_$($Ssl.SslProtocol)|expires_$($Certificate.NotAfter.ToUniversalTime().ToString('yyyy-MM-dd'))"
    }
    Write-Output "CERTIFICATE|subject=$($Certificate.Subject)|issuer=$($Certificate.Issuer)|thumbprint=$($Certificate.Thumbprint)"
}
catch {
    Add-Fail "tls_certificate|$($_.Exception.GetType().Name)"
}
finally {
    if ($null -ne $Ssl) { $Ssl.Dispose() }
    if ($null -ne $Tcp) { $Tcp.Dispose() }
}

$Handler = [System.Net.Http.HttpClientHandler]::new()
$Handler.AllowAutoRedirect = $false
$Handler.UseProxy = $false
$Client = [System.Net.Http.HttpClient]::new($Handler)
$Client.Timeout = [TimeSpan]::FromSeconds(20)

try {
    $RootResponse = $Client.GetAsync("https://$Domain/").GetAwaiter().GetResult()
    $RootCode = [int]$RootResponse.StatusCode
    if ($RootCode -in @(200, 301, 302, 303, 307, 308)) {
        Add-Pass "https_root|http_$RootCode"
    }
    else {
        Add-Fail "https_root|http_$RootCode"
    }

    $RequiredHeaders = @(
        'Strict-Transport-Security',
        'X-Content-Type-Options',
        'X-Frame-Options',
        'Referrer-Policy',
        'Permissions-Policy',
        'Content-Security-Policy'
    )
    foreach ($Header in $RequiredHeaders) {
        if ($RootResponse.Headers.Contains($Header) -or $RootResponse.Content.Headers.Contains($Header)) {
            Add-Pass "security_header|$($Header.ToLowerInvariant())"
        }
        else {
            Add-Fail "security_header_missing|$($Header.ToLowerInvariant())"
        }
    }
    $RootResponse.Dispose()

    $ExpectedStatuses = [ordered]@{
        '/docs' = 404
        '/redoc' = 404
        '/openapi.json' = 404
        '/api/v1/admin/codex-worker/' = 404
        '/api/v1/admin/dwg-quantity-trial/' = 404
        '/health/ready' = 403
    }
    foreach ($Entry in $ExpectedStatuses.GetEnumerator()) {
        $Response = $Client.GetAsync("https://$Domain$($Entry.Key)").GetAwaiter().GetResult()
        $Code = [int]$Response.StatusCode
        $Response.Dispose()
        if ($Code -eq $Entry.Value) {
            Add-Pass "sensitive_route|$($Entry.Key)|http_$Code"
        }
        else {
            Add-Fail "sensitive_route|$($Entry.Key)|http_$Code|expected_$($Entry.Value)"
        }
    }
}
catch {
    Add-Fail "https_request_gate|$($_.Exception.GetType().Name)"
}
finally {
    $Client.Dispose()
    $Handler.Dispose()
}

$HttpTcp = $null
try {
    $HttpTcp = [System.Net.Sockets.TcpClient]::new()
    $HttpConnect = $HttpTcp.ConnectAsync($ExpectedIp, 80)
    if ($HttpConnect.Wait(5000) -and $HttpTcp.Connected) {
        Add-Fail 'public_http_80|reachable'
    }
    else {
        Add-Pass 'public_http_80|blocked'
    }
}
catch {
    Add-Pass 'public_http_80|blocked'
}
finally {
    if ($null -ne $HttpTcp) { $HttpTcp.Dispose() }
}

$SshTcp = $null
try {
    $SshTcp = [System.Net.Sockets.TcpClient]::new()
    $SshConnect = $SshTcp.ConnectAsync($ExpectedIp, 22)
    if ($SshConnect.Wait(5000) -and $SshTcp.Connected) {
        Add-Fail 'public_ssh_22|reachable'
    }
    else {
        Add-Pass 'public_ssh_22|blocked'
    }
}
catch {
    Add-Pass 'public_ssh_22|blocked'
}
finally {
    if ($null -ne $SshTcp) { $SshTcp.Dispose() }
}

$UnexpectedTcp = $null
$UnexpectedSsl = $null
try {
    $UnexpectedTcp = [System.Net.Sockets.TcpClient]::new()
    $UnexpectedConnect = $UnexpectedTcp.ConnectAsync($ExpectedIp, 443)
    if (-not $UnexpectedConnect.Wait(10000)) {
        throw 'Unexpected-SNI connect timeout'
    }
    $AcceptAnyCertificate = {
        param($Sender, $Certificate, $Chain, $Errors)
        return $true
    }
    $UnexpectedSsl = [System.Net.Security.SslStream]::new(
        $UnexpectedTcp.GetStream(),
        $false,
        $AcceptAnyCertificate
    )
    $UnexpectedSsl.ReadTimeout = 10000
    $UnexpectedSsl.WriteTimeout = 10000
    $UnexpectedSsl.AuthenticateAsClient('unexpected.invalid')
    $Request = [Text.Encoding]::ASCII.GetBytes(
        "GET / HTTP/1.1`r`nHost: unexpected.invalid`r`nConnection: close`r`n`r`n"
    )
    $UnexpectedSsl.Write($Request, 0, $Request.Length)
    $Buffer = [byte[]]::new(1)
    try {
        $ReadCount = $UnexpectedSsl.Read($Buffer, 0, 1)
        if ($ReadCount -eq 0) {
            Add-Pass 'unexpected_sni_host|connection_closed_without_http_response'
        }
        else {
            Add-Fail 'unexpected_sni_host|received_http_response'
        }
    }
    catch [System.IO.IOException] {
        Add-Pass 'unexpected_sni_host|connection_reset_without_http_response'
    }
}
catch {
    Add-Fail "unexpected_sni_host|$($_.Exception.GetType().Name)"
}
finally {
    if ($null -ne $UnexpectedSsl) { $UnexpectedSsl.Dispose() }
    if ($null -ne $UnexpectedTcp) { $UnexpectedTcp.Dispose() }
}

if ($Failures.Count -gt 0) {
    Write-Output "RESULT=FAIL|count_$($Failures.Count)"
    exit 1
}

Write-Output 'RESULT=PASS|phase6_external_https_readonly_gate'
