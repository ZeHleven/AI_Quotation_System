# AI Middle Office - one-click startup orchestrator
# Starts FastAPI and Celery after CentOS dependencies are reachable.

[CmdletBinding()]
param(
    [string]$CentosHost = "",
    [int]$AppPort = 9000,
    [string]$HostAddress = "",
    [int]$RemoteTimeoutSeconds = 180,
    [int]$LocalTimeoutSeconds = 120,
    [int]$ReadyStabilitySeconds = 8,
    [switch]$Lan,
    [switch]$NoBrowser,
    [switch]$SkipRemoteWait,
    [switch]$SkipCelery,
    [switch]$SkipBidIntakeAgent,
    [switch]$SkipMigrations,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$processPath = [Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $processPath) {
    $processPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
}
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
if ($processPath) {
    [Environment]::SetEnvironmentVariable("Path", $processPath, "Process")
}

$WorkDir = $PSScriptRoot
$LogDir = Join-Path $WorkDir "logs"
$EnvFile = Join-Path $WorkDir ".env"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

function Enter-StartupInstanceLock {
    param([string]$Path)

    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $content = "pid={0}; started_at={1}`r`n" -f (
            $PID,
            (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        )
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
        $stream.SetLength(0)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush()
        return $stream
    } catch [System.IO.IOException] {
        Write-Host (
            "[SKIP] Another start_all.ps1 instance is already running. " +
            "This duplicate startup request will exit."
        ) -ForegroundColor Yellow
        return $null
    }
}

$StartupLockPath = Join-Path $LogDir "start_all.lock"
$StartupLockStream = Enter-StartupInstanceLock -Path $StartupLockPath
if (-not $StartupLockStream) {
    exit 0
}

function Get-DotEnvValue {
    param(
        [string]$Name,
        [string]$Default = ""
    )
    if (-not (Test-Path $EnvFile)) { return $Default }
    $prefix = "$Name="
    $line = Get-Content `
        -Path $EnvFile `
        -Encoding UTF8 `
        -ErrorAction SilentlyContinue |
        Where-Object { $_ -and $_.TrimStart().StartsWith($prefix) } |
        Select-Object -First 1
    if (-not $line) { return $Default }
    $value = $line.Substring($prefix.Length).Trim()
    return $value.Trim('"').Trim("'")
}

function Import-DotEnvToProcess {
    if (-not (Test-Path $EnvFile)) { return }
    Get-Content `
        -Path $EnvFile `
        -Encoding UTF8 `
        -ErrorAction SilentlyContinue |
        ForEach-Object {
            $line = $_
            if (-not $line) { return }
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith("#")) { return }
            $parts = $trimmed.Split("=", 2)
            if ($parts.Count -ne 2) { return }
            $name = $parts[0].Trim().TrimStart([char]0xFEFF)
            if (-not $name) { return }
            $value = $parts[1].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
}

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
    throw "python.exe not found. Please install Miniconda or update start_all.ps1."
}

function Test-TcpPort {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutMs = 1500
    )
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $ok) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async)
        $client.Close()
        return $true
    } catch {
        if ($client) { $client.Close() }
        return $false
    }
}

function Get-ListeningProcessIds {
    param([int]$Port)

    $ids = @()
    try {
        $connections = Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction Stop
        $ids += $connections | Select-Object -ExpandProperty OwningProcess
    } catch {
        $pattern = (
            "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        )
        foreach ($line in (& netstat.exe -ano -p tcp 2>$null)) {
            if ($line -match $pattern) {
                $ids += [int]$Matches[1]
            }
        }
    }

    return @(
        $ids |
            Where-Object { $_ -and $_ -gt 0 } |
            Sort-Object -Unique
    )
}

function Resolve-BindHost {
    param(
        [string]$RequestedHost,
        [switch]$UseLan
    )

    if ($RequestedHost) {
        if ($UseLan -and $RequestedHost -in @("127.0.0.1", "localhost")) {
            throw "-Lan cannot be combined with -HostAddress $RequestedHost. Use -Lan alone, -HostAddress 0.0.0.0, or a real LAN IPv4 address."
        }
        return $RequestedHost
    }
    if ($UseLan) {
        return "0.0.0.0"
    }
    return "127.0.0.1"
}

function Get-ProbeHost {
    param([string]$BindHost)
    if ($BindHost -in @("0.0.0.0", "::", "", "localhost")) {
        return "127.0.0.1"
    }
    return $BindHost
}

function Get-LanIpAddresses {
    $addresses = @()
    try {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -and
                $_.IPAddress -ne "127.0.0.1" -and
                -not $_.IPAddress.StartsWith("169.254.") -and
                $_.PrefixOrigin -ne "WellKnown"
            } |
            Sort-Object -Property InterfaceAlias, IPAddress |
            Select-Object -ExpandProperty IPAddress -Unique
    } catch {
        try {
            $addresses = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } |
                ForEach-Object { $_.IPAddressToString } |
                Where-Object { $_ -ne "127.0.0.1" -and -not $_.StartsWith("169.254.") } |
                Select-Object -Unique
        } catch {
            $addresses = @()
        }
    }
    return @($addresses)
}

function Get-AccessUrlLines {
    $lines = @("Local URL: http://127.0.0.1:$AppPort/")
    if ($HostAddress -eq "0.0.0.0" -or $Lan) {
        $lanIps = @(Get-LanIpAddresses)
        if ($lanIps.Count -eq 0) {
            $lines += "LAN URL:   no non-loopback IPv4 address detected"
        } else {
            foreach ($ip in $lanIps) {
                $lines += "LAN URL:   http://$($ip):$AppPort/"
            }
        }
    } elseif ($HostAddress -notin @("127.0.0.1", "localhost")) {
        $lines += "LAN URL:   http://$($HostAddress):$AppPort/"
    }
    return $lines
}

function Write-AccessInfoFile {
    $path = Join-Path $LogDir "current_access_urls.txt"
    $lines = @(
        "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "Mode: $StartupMode",
        "Bind: $HostAddress",
        "Health: http://${ProbeHost}:$AppPort/health/ready"
    )
    $lines += Get-AccessUrlLines
    Set-Content -Path $path -Value $lines -Encoding UTF8
    Write-Host " Access info: $path"
}

function Test-CommandUsesRequestedBindHost {
    param([string]$CommandLine)
    if (-not $CommandLine) { return $false }
    return ($CommandLine -like "*--host $HostAddress*")
}

function Wait-TcpPort {
    param(
        [string]$Name,
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -TargetHost $TargetHost -Port $Port) {
            Write-Host "[OK] $Name reachable at ${TargetHost}:$Port" -ForegroundColor Green
            return
        }
        Write-Host "[WAIT] $Name ${TargetHost}:$Port ..." -ForegroundColor Yellow
        Start-Sleep -Seconds 3
    }
    throw "$Name is not reachable at ${TargetHost}:$Port after ${TimeoutSeconds}s. Check CentOS network, Docker containers, or firewall."
}

function Wait-FastApiReady {
    param([int]$TimeoutSeconds)
    $url = "http://${ProbeHost}:$AppPort/health/ready"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri $url -TimeoutSec 5
            $lastStatus = $response.status
            if ($response.status -eq "ready") {
                if ($ReadyStabilitySeconds -gt 0) {
                    Write-Host "[WAIT] FastAPI ready once; confirming stability for ${ReadyStabilitySeconds}s..." -ForegroundColor Yellow
                    Start-Sleep -Seconds $ReadyStabilitySeconds
                    $confirm = Invoke-RestMethod -Uri $url -TimeoutSec 5
                    $lastStatus = $confirm.status
                    if ($confirm.status -eq "ready") {
                        Write-Host "[OK] FastAPI ready: $url" -ForegroundColor Green
                        return $confirm
                    }
                    Write-Host "[WAIT] FastAPI health changed to $($confirm.status)" -ForegroundColor Yellow
                } else {
                    Write-Host "[OK] FastAPI ready: $url" -ForegroundColor Green
                    return $response
                }
            }
            Write-Host "[WAIT] FastAPI health is $($response.status)" -ForegroundColor Yellow
        } catch {
            Write-Host "[WAIT] FastAPI health endpoint not ready..." -ForegroundColor Yellow
        }
        Start-Sleep -Seconds 3
    }
    throw "FastAPI did not become ready after ${TimeoutSeconds}s. Last status: $lastStatus"
}

function Start-FastApi {
    param([string]$PythonPath)

    $portAlreadyListening = Test-TcpPort -TargetHost $ProbeHost -Port $AppPort -TimeoutMs 500

    $pidFile = Join-Path $LogDir "fastapi.pid"
    if (Test-Path $pidFile) {
        $oldPidText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        $oldPid = 0
        if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
            $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($oldProcess) {
                if ($Restart) {
                    Write-Host "[RESTART] Stopping FastAPI pid from pid file: $oldPid" -ForegroundColor Yellow
                    Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 2
                    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
                    $oldProcess = $null
                }
            }
            if ($oldProcess) {
                $oldCommand = (Get-CimInstance Win32_Process -Filter "ProcessId=$oldPid" -ErrorAction SilentlyContinue).CommandLine
                if ($oldCommand -and $oldCommand -like "*uvicorn*" -and $oldCommand -like "*app.main:app*") {
                    if ($StartupMode -eq "lan-trial" -and -not (Test-CommandUsesRequestedBindHost -CommandLine $oldCommand)) {
                        throw "FastAPI is already running with a different bind host. Run start_all.ps1 -Lan -Restart to switch into LAN trial mode."
                    }
                    Write-Host "[OK] FastAPI already appears to be running with pid: $oldPid" -ForegroundColor Green
                    return
                }
                $listenerProcessIds = @(
                    Get-ListeningProcessIds -Port $AppPort
                )
                if (
                    $StartupMode -eq "local-dev" -and
                    $portAlreadyListening -and
                    $listenerProcessIds -contains $oldPid
                ) {
                    Write-Host (
                        "[OK] FastAPI pid file matches the port $AppPort " +
                        "listener: pid=$oldPid"
                    ) -ForegroundColor Green
                    return
                }
            }
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }

    if ($portAlreadyListening -and -not $Restart) {
        if ($StartupMode -eq "lan-trial") {
            throw "FastAPI port $AppPort is already listening. Run start_all.ps1 -Lan -Restart to switch into LAN trial mode."
        }
        Write-Host "[OK] FastAPI port $AppPort is already listening" -ForegroundColor Green
        return
    }

    if (Test-TcpPort -TargetHost $ProbeHost -Port $AppPort -TimeoutMs 500) {
        throw "FastAPI port $AppPort is still listening after restart cleanup. Stop the old process first, then run start_all.ps1 again."
    }

    $outLog = Join-Path $LogDir ("fastapi_{0}.out.log" -f (Get-Date -Format "yyyyMMdd"))
    $errLog = Join-Path $LogDir ("fastapi_{0}.err.log" -f (Get-Date -Format "yyyyMMdd"))
    $args = @("-m", "uvicorn", "app.main:app", "--host", $HostAddress, "--port", "$AppPort")
    $process = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList $args `
        -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -WindowStyle Hidden `
        -PassThru
    Set-Content -Path $pidFile -Value $process.Id -Encoding ASCII
    Write-Host "[START] FastAPI pid=$($process.Id), logs=$LogDir" -ForegroundColor Cyan
}

function Start-Celery {
    if ($SkipCelery) {
        Write-Host "[SKIP] Celery startup skipped" -ForegroundColor Yellow
        return
    }
    $launcher = Join-Path $WorkDir "start_celery_worker.ps1"
    if (-not (Test-Path $launcher)) {
        throw "Celery launcher not found: $launcher"
    }
    $celeryArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher)
    if ($Restart) {
        $celeryArgs += "-Restart"
    }
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $celeryArgs `
        -WorkingDirectory $WorkDir `
        -WindowStyle Hidden | Out-Null
    Write-Host "[START] Celery worker launcher invoked" -ForegroundColor Cyan
}

function Start-BidIntakeAgent {
    if ($SkipBidIntakeAgent) {
        Write-Host (
            "[SKIP] Bid-intake Agent startup skipped"
        ) -ForegroundColor Yellow
        return
    }
    $runtimeEnabled = (
        Get-DotEnvValue `
            -Name "BID_INTAKE_AGENT_RUNTIME_ENABLED" `
            -Default "false"
    ).ToLowerInvariant()
    if ($runtimeEnabled -notin @("1", "true", "yes", "on")) {
        Write-Host (
            "[SKIP] Bid-intake Agent runtime is disabled"
        ) -ForegroundColor Yellow
        return
    }
    $launcher = Join-Path $WorkDir "start_bid_intake_agent.ps1"
    if (-not (Test-Path $launcher)) {
        throw "Bid-intake Agent launcher not found: $launcher"
    }
    $agentArgs = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $launcher
    )
    if ($Restart) {
        $agentArgs += "-Restart"
    }
    & powershell.exe @agentArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Bid-intake Agent runtime startup failed"
    }
}

function Invoke-DatabaseMigrations {
    param([string]$PythonPath)

    if ($SkipMigrations) {
        Write-Host "[SKIP] Database migrations skipped by parameter" -ForegroundColor Yellow
        return
    }

    $autoRun = (Get-DotEnvValue -Name "AUTO_RUN_DB_MIGRATIONS" -Default "true").ToLowerInvariant()
    if ($autoRun -notin @("1", "true", "yes", "on")) {
        Write-Host "[SKIP] Database migrations skipped because AUTO_RUN_DB_MIGRATIONS is not true" -ForegroundColor Yellow
        return
    }

    $alembicIni = Join-Path $WorkDir "alembic.ini"
    if (-not (Test-Path $alembicIni)) {
        throw "alembic.ini not found: $alembicIni"
    }

    Write-Host "[INFO] Running database migrations..." -ForegroundColor Cyan
    & $PythonPath -c "from alembic.config import main; main()" -c $alembicIni upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migrations failed. Run: pip install -r requirements.txt"
    }
    Write-Host "[OK] Database migrations are up to date" -ForegroundColor Green
}

Set-Location $WorkDir

if (-not $CentosHost) {
    if ($env:MIDDLE_OFFICE_CENTOS_HOST) {
        $CentosHost = $env:MIDDLE_OFFICE_CENTOS_HOST
    } else {
        $CentosHost = "192.168.88.128"
    }
}

$HostAddress = Resolve-BindHost -RequestedHost $HostAddress -UseLan:$Lan
$ProbeHost = Get-ProbeHost -BindHost $HostAddress
$StartupMode = "local-dev"
if ($HostAddress -eq "0.0.0.0" -or $HostAddress -notin @("127.0.0.1", "localhost")) {
    $StartupMode = "lan-trial"
}

$PythonPath = Find-Python
Import-DotEnvToProcess
$minioEnabled = (Get-DotEnvValue -Name "MINIO_ENABLED" -Default "false").ToLowerInvariant()

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " AI Middle Office startup"
Write-Host " WorkDir: $WorkDir"
Write-Host " Python:  $PythonPath"
Write-Host " CentOS:  $CentosHost"
Write-Host " Mode:    $StartupMode"
Write-Host " Bind:    $HostAddress"
Write-Host "========================================" -ForegroundColor Cyan

if (-not $SkipRemoteWait) {
    Wait-TcpPort -Name "MySQL" -TargetHost $CentosHost -Port 5455 -TimeoutSeconds $RemoteTimeoutSeconds
    Wait-TcpPort -Name "Redis" -TargetHost $CentosHost -Port 6380 -TimeoutSeconds $RemoteTimeoutSeconds
    Wait-TcpPort -Name "RAG service" -TargetHost $CentosHost -Port 8001 -TimeoutSeconds $RemoteTimeoutSeconds
    Wait-TcpPort -Name "n8n" -TargetHost $CentosHost -Port 5678 -TimeoutSeconds $RemoteTimeoutSeconds
    if ($minioEnabled -in @("1", "true", "yes", "on")) {
        Wait-TcpPort -Name "MinIO" -TargetHost $CentosHost -Port 9002 -TimeoutSeconds $RemoteTimeoutSeconds
    } else {
        Write-Host "[SKIP] MinIO wait skipped because MINIO_ENABLED is not true" -ForegroundColor Yellow
    }
} else {
    Write-Host "[SKIP] Remote dependency wait skipped" -ForegroundColor Yellow
}

Invoke-DatabaseMigrations -PythonPath $PythonPath
Start-Celery
Start-FastApi -PythonPath $PythonPath
$ready = Wait-FastApiReady -TimeoutSeconds $LocalTimeoutSeconds
$bidIntakeAgentStatus = "ok"
try {
    Start-BidIntakeAgent
} catch {
    $bidIntakeAgentStatus = "degraded"
    $agentError = $_.Exception.Message
    Write-Warning (
        "Bid-intake Agent startup failed, but FastAPI remains available: " +
        $agentError
    )
    $agentLog = Join-Path $LogDir (
        "bid_intake_startup_warning_{0}.log" -f (
            Get-Date -Format "yyyyMMdd"
        )
    )
    $agentLogLine = "[{0}] {1}" -f (
        Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    ), $agentError
    Add-Content `
        -LiteralPath $agentLog `
        -Encoding UTF8 `
        -Value $agentLogLine
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " System is ready"
foreach ($line in (Get-AccessUrlLines)) {
    Write-Host " $line"
}
Write-AccessInfoFile
Write-Host " Queue: $($ready.task_queue.mode) / ok=$($ready.task_queue.ok)"
Write-Host " Bid-intake Agent: $bidIntakeAgentStatus"
Write-Host " Logs: $LogDir"
if ($StartupMode -eq "lan-trial") {
    Write-Host " Firewall: ensure Windows allows inbound TCP $AppPort on the current private LAN before other PCs connect."
}
Write-Host "========================================" -ForegroundColor Green

if (-not $NoBrowser) {
    Start-Process "http://${ProbeHost}:$AppPort/"
}

$StartupLockStream.Dispose()
