# Bid-intake Agent runtime launcher.
# Starts the project-scoped MCP server and the dedicated LangGraph worker.

[CmdletBinding()]
param(
    [int]$McpPort = 8012,
    [int]$StartupTimeoutSeconds = 45,
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ge 7) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$processPath = [Environment]::GetEnvironmentVariable(
    "Path",
    "Process"
)
if (-not $processPath) {
    $processPath = [Environment]::GetEnvironmentVariable(
        "PATH",
        "Process"
    )
}
[Environment]::SetEnvironmentVariable("PATH", $null, "Process")
if ($processPath) {
    [Environment]::SetEnvironmentVariable(
        "Path",
        $processPath,
        "Process"
    )
}

$WorkDir = $PSScriptRoot
$EnvFile = Join-Path $WorkDir ".env"
$LogDir = Join-Path $WorkDir "logs"
$McpPidFile = Join-Path $LogDir "bid_intake_mcp.pid"
$WorkerPidFile = Join-Path $LogDir "bid_intake_worker.pid"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
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
            [Environment]::SetEnvironmentVariable(
                $name,
                $value,
                "Process"
            )
        }
}

function Test-Enabled {
    param([string]$Value)
    return ($Value.Trim().ToLowerInvariant() -in @(
        "1",
        "true",
        "yes",
        "on"
    ))
}

function Get-TrackedProcess {
    param([string]$PidFile)
    if (-not (Test-Path $PidFile)) { return $null }
    $pidText = Get-Content -Path $PidFile -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $trackedPid = 0
    if (-not [int]::TryParse($pidText, [ref]$trackedPid)) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    $process = Get-Process -Id $trackedPid -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $process
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    try {
        $processInfo = Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ProcessId = $ProcessId" `
            -ErrorAction Stop
        if ($processInfo) {
            return [string]$processInfo.CommandLine
        }
    } catch {
        return ""
    }
    return ""
}

function Test-ProcessDescendsFrom {
    param(
        [int]$ProcessId,
        [int]$AncestorProcessId
    )
    if ($ProcessId -le 0 -or $AncestorProcessId -le 0) {
        return $false
    }

    $currentProcessId = $ProcessId
    for ($depth = 0; $depth -lt 8; $depth += 1) {
        try {
            $processInfo = Get-CimInstance `
                -ClassName Win32_Process `
                -Filter "ProcessId = $currentProcessId" `
                -ErrorAction Stop
        } catch {
            return $false
        }
        if (-not $processInfo) {
            return $false
        }
        $parentProcessId = [int]$processInfo.ParentProcessId
        if ($parentProcessId -eq $AncestorProcessId) {
            return $true
        }
        if ($parentProcessId -le 0 -or $parentProcessId -eq $currentProcessId) {
            return $false
        }
        $currentProcessId = $parentProcessId
    }
    return $false
}

function Get-VenvBasePythonExecutable {
    param([string]$ExpectedExecutable)

    try {
        $scriptsDir = Split-Path -Parent $ExpectedExecutable
        $venvDir = Split-Path -Parent $scriptsDir
        $configPath = Join-Path $venvDir "pyvenv.cfg"
        if (-not (Test-Path -LiteralPath $configPath)) {
            return ""
        }
        $line = Get-Content `
            -LiteralPath $configPath `
            -Encoding UTF8 `
            -ErrorAction Stop |
            Where-Object { $_ -match "^\s*executable\s*=" } |
            Select-Object -First 1
        if (-not $line) {
            return ""
        }
        $value = ($line -split "=", 2)[1].Trim()
        if ($value -and (Test-Path -LiteralPath $value)) {
            return [IO.Path]::GetFullPath($value)
        }
    } catch {
        return ""
    }
    return ""
}

function Test-ExpectedProjectProcess {
    param(
        [int]$ProcessId,
        [string]$ExpectedScript,
        [string]$ExpectedExecutable,
        [int]$ExpectedAncestorProcessId = 0,
        [datetime]$LaunchedAfter = [datetime]::MinValue
    )
    if (
        $ExpectedAncestorProcessId -gt 0 -and
        (Test-ProcessDescendsFrom `
            -ProcessId $ProcessId `
            -AncestorProcessId $ExpectedAncestorProcessId)
    ) {
        return $true
    }

    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    if ($commandLine) {
        $scriptName = [IO.Path]::GetFileName($ExpectedScript)
        return $commandLine.IndexOf(
            $scriptName,
            [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    }

    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $actualExecutable = [IO.Path]::GetFullPath($process.Path)
        $expectedExecutablePath = [IO.Path]::GetFullPath(
            $ExpectedExecutable
        )
        if ($actualExecutable.Equals(
            $expectedExecutablePath,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            return $true
        }

        if (
            $LaunchedAfter -gt [datetime]::MinValue -and
            $process.StartTime -ge $LaunchedAfter.AddSeconds(-5)
        ) {
            $venvBaseExecutable = Get-VenvBasePythonExecutable `
                -ExpectedExecutable $ExpectedExecutable
            if (
                $venvBaseExecutable -and
                $actualExecutable.Equals(
                    $venvBaseExecutable,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) {
                return $true
            }
        }
        return $false
    } catch {
        return $false
    }
}

function Stop-TrackedProcess {
    param(
        [string]$Name,
        [string]$PidFile,
        [string]$ExpectedScript,
        [string]$ExpectedExecutable
    )
    $process = Get-TrackedProcess -PidFile $PidFile
    if ($process) {
        $isExpected = Test-ExpectedProjectProcess `
            -ProcessId $process.Id `
            -ExpectedScript $ExpectedScript `
            -ExpectedExecutable $ExpectedExecutable
        if (-not $isExpected) {
            Write-Host (
                "[WARN] Ignoring stale $Name pid file; pid=" +
                "$($process.Id) is not the expected project process."
            ) -ForegroundColor Yellow
            Remove-Item `
                -LiteralPath $PidFile `
                -Force `
                -ErrorAction SilentlyContinue
            return
        }
        Write-Host (
            "[RESTART] Stopping $Name pid=$($process.Id)"
        ) -ForegroundColor Yellow
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit(10000) | Out-Null
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Get-TcpPortOwnerProcessId {
    param([int]$Port)
    try {
        $connection = Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction Stop |
            Where-Object { $_.OwningProcess -gt 0 } |
            Select-Object -First 1
        if ($connection) {
            return [int]$connection.OwningProcess
        }
    } catch {
        # Standard users can receive access denied from the CIM-backed
        # Get-NetTCPConnection cmdlet. Fall through to netstat, which exposes
        # the listener PID without requiring administrator privileges.
    }

    try {
        $pattern = (
            "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        )
        foreach ($line in (& netstat.exe -ano -p tcp 2>$null)) {
            if ($line -match $pattern) {
                return [int]$Matches[1]
            }
        }
    } catch {
        return 0
    }
    return 0
}

function Stop-ProjectMcpPortOwner {
    param(
        [int]$Port,
        [string]$ExpectedExecutable
    )
    $ownerProcessId = Get-TcpPortOwnerProcessId -Port $Port
    if ($ownerProcessId -le 0) {
        return
    }
    $isExpected = Test-ExpectedProjectProcess `
        -ProcessId $ownerProcessId `
        -ExpectedScript "tender_evidence_mcp_server.py" `
        -ExpectedExecutable $ExpectedExecutable
    if (-not $isExpected) {
        throw (
            "Port $Port is owned by pid=$ownerProcessId, which is not " +
            "the project Tender Evidence MCP. It was not stopped."
        )
    }

    Write-Host (
        "[RESTART] Stopping stale Tender Evidence MCP listener " +
        "pid=$ownerProcessId on port $Port"
    ) -ForegroundColor Yellow
    $process = Get-Process -Id $ownerProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $ownerProcessId -Force
        $process.WaitForExit(10000) | Out-Null
    }
}

function Test-TcpPort {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutMs = 1000
    )
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect(
            $TargetHost,
            $Port,
            $null,
            $null
        )
        $connected = $async.AsyncWaitHandle.WaitOne(
            $TimeoutMs,
            $false
        )
        if (-not $connected) {
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

function Wait-TcpPort {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutSeconds
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -TargetHost $TargetHost -Port $Port) {
            return
        }
        Start-Sleep -Seconds 1
    }
    throw (
        "Tender Evidence MCP did not listen on " +
        "${TargetHost}:$Port within ${TimeoutSeconds}s."
    )
}

function Wait-TcpPortFree {
    param(
        [string]$TargetHost,
        [int]$Port,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-TcpPort -TargetHost $TargetHost -Port $Port)) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw (
        "Port $Port did not become free within ${TimeoutSeconds}s " +
        "after stopping the tracked Tender Evidence MCP."
    )
}

function Wait-WorkerPidFile {
    param(
        [string]$PidFile,
        [datetime]$LaunchedAfter,
        [int]$TimeoutSeconds = 45
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $PidFile) {
            $pidText = Get-Content `
                -LiteralPath $PidFile `
                -ErrorAction SilentlyContinue |
                Select-Object -First 1
            $actualPid = 0
            if ([int]::TryParse($pidText, [ref]$actualPid)) {
                $actualProcess = Get-Process `
                    -Id $actualPid `
                    -ErrorAction SilentlyContinue
                if ($actualProcess) {
                    if ($actualProcess.StartTime -lt $LaunchedAfter.AddSeconds(-5)) {
                        throw (
                            "Worker pid file points to an older process: " +
                            "pid=$actualPid."
                        )
                    }
                    return $actualProcess
                }
            }
        }
        Start-Sleep -Milliseconds 250
    }
    throw (
        "Bid-intake Worker did not publish its actual PID within " +
        "${TimeoutSeconds}s."
    )
}

function New-RuntimeSecret {
    $bytes = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes)
}

Import-DotEnvToProcess
$runtimeEnabled = Get-DotEnvValue `
    -Name "BID_INTAKE_AGENT_RUNTIME_ENABLED" `
    -Default "false"
if (-not (Test-Enabled -Value $runtimeEnabled)) {
    Write-Host (
        "[SKIP] Bid-intake Agent runtime is disabled"
    ) -ForegroundColor Yellow
    exit 0
}

$AgentPython = Get-DotEnvValue -Name "BID_INTAKE_AGENT_PYTHON"
if (-not $AgentPython) {
    $AgentPython = Join-Path $WorkDir ".venv-agent\Scripts\python.exe"
}
if (-not (Test-Path $AgentPython)) {
    throw (
        "Agent Python not found: $AgentPython. Create it with: " +
        "python -m venv .venv-agent --system-site-packages; " +
        ".\.venv-agent\Scripts\python.exe -m pip install " +
        "-r requirements-agent.in"
    )
}

& $AgentPython -c "import langgraph, langchain_core, mcp"
if ($LASTEXITCODE -ne 0) {
    throw "Agent dependencies are incomplete in $AgentPython"
}

$mcpProcess = Get-TrackedProcess -PidFile $McpPidFile
$workerProcess = Get-TrackedProcess -PidFile $WorkerPidFile
if ($Restart) {
    Stop-TrackedProcess `
        -Name "bid-intake worker" `
        -PidFile $WorkerPidFile `
        -ExpectedScript "bid_intake_agent_worker.py" `
        -ExpectedExecutable $AgentPython
    Stop-TrackedProcess `
        -Name "tender evidence MCP" `
        -PidFile $McpPidFile `
        -ExpectedScript "tender_evidence_mcp_server.py" `
        -ExpectedExecutable $AgentPython
    if (Test-TcpPort -TargetHost "127.0.0.1" -Port $McpPort) {
        Stop-ProjectMcpPortOwner `
            -Port $McpPort `
            -ExpectedExecutable $AgentPython
    }
    Wait-TcpPortFree `
        -TargetHost "127.0.0.1" `
        -Port $McpPort `
        -TimeoutSeconds 30
    $mcpProcess = $null
    $workerProcess = $null
} elseif ($mcpProcess -and $workerProcess) {
    Write-Host (
        "[OK] Bid-intake Agent runtime already running: " +
        "MCP pid=$($mcpProcess.Id), Worker pid=$($workerProcess.Id)"
    ) -ForegroundColor Green
    exit 0
} elseif ($mcpProcess -or $workerProcess) {
    throw (
        "Bid-intake runtime is only partially running. " +
        "Run start_bid_intake_agent.ps1 -Restart."
    )
}

if (Test-TcpPort -TargetHost "127.0.0.1" -Port $McpPort) {
    throw (
        "Port $McpPort is already occupied by an untracked process. " +
        "Stop it or choose another MCP port."
    )
}

if (-not $env:TENDER_MCP_JWT_SECRET) {
    $env:TENDER_MCP_JWT_SECRET = New-RuntimeSecret
}
if (-not $env:TENDER_MCP_ISSUER) {
    $env:TENDER_MCP_ISSUER = "http://127.0.0.1:$McpPort"
}
if (-not $env:TENDER_MCP_AUDIENCE) {
    $env:TENDER_MCP_AUDIENCE = "http://127.0.0.1:$McpPort/mcp"
}
if (-not $env:BID_INTAKE_MCP_URL) {
    $env:BID_INTAKE_MCP_URL = "http://127.0.0.1:$McpPort/mcp"
}
$env:BID_INTAKE_AGENT_RUNTIME_ENABLED = "true"

$dateSuffix = Get-Date -Format "yyyyMMdd"
$mcpOutLog = Join-Path $LogDir "bid_intake_mcp_${dateSuffix}.out.log"
$mcpErrLog = Join-Path $LogDir "bid_intake_mcp_${dateSuffix}.err.log"
$workerOutLog = Join-Path $LogDir (
    "bid_intake_worker_${dateSuffix}.out.log"
)
$workerErrLog = Join-Path $LogDir (
    "bid_intake_worker_${dateSuffix}.err.log"
)

$mcpScript = Join-Path $WorkDir "scripts\tender_evidence_mcp_server.py"
$mcpArgs = @(
    $mcpScript,
    "--transport",
    "streamable-http",
    "--repository",
    "database",
    "--search-mode",
    "auto",
    "--host",
    "127.0.0.1",
    "--port",
    "$McpPort"
)
$mcpLaunchStartedAt = Get-Date
$mcpProcess = Start-Process `
    -FilePath $AgentPython `
    -ArgumentList $mcpArgs `
    -WorkingDirectory $WorkDir `
    -RedirectStandardOutput $mcpOutLog `
    -RedirectStandardError $mcpErrLog `
    -WindowStyle Hidden `
    -PassThru
Set-Content -Path $McpPidFile -Value $mcpProcess.Id -Encoding ASCII

try {
    Wait-TcpPort `
        -TargetHost "127.0.0.1" `
        -Port $McpPort `
        -TimeoutSeconds $StartupTimeoutSeconds
    $mcpListenerProcessId = Get-TcpPortOwnerProcessId -Port $McpPort
    if ($mcpListenerProcessId -le 0) {
        throw (
            "Tender Evidence MCP is reachable, but its listening process " +
            "could not be identified on port $McpPort."
        )
    }
    $listenerProcessInfo = Get-CimInstance `
        -ClassName Win32_Process `
        -Filter "ProcessId = $mcpListenerProcessId" `
        -ErrorAction SilentlyContinue
    $listenerParentProcessId = 0
    if ($listenerProcessInfo) {
        $listenerParentProcessId = [int]$listenerProcessInfo.ParentProcessId
    }
    Write-Host (
        "[INFO] Tender Evidence MCP identity: launcher_pid=" +
        "$($mcpProcess.Id), listener_pid=$mcpListenerProcessId, " +
        "listener_parent_pid=$listenerParentProcessId"
    ) -ForegroundColor Cyan
    $isExpectedListener = Test-ExpectedProjectProcess `
        -ProcessId $mcpListenerProcessId `
        -ExpectedScript "tender_evidence_mcp_server.py" `
        -ExpectedExecutable $AgentPython `
        -ExpectedAncestorProcessId $mcpProcess.Id `
        -LaunchedAfter $mcpLaunchStartedAt
    if (-not $isExpectedListener) {
        throw (
            "Port $McpPort became reachable through unexpected " +
            "pid=$mcpListenerProcessId."
        )
    }
    Set-Content `
        -Path $McpPidFile `
        -Value $mcpListenerProcessId `
        -Encoding ASCII
} catch {
    if (-not $mcpProcess.HasExited) {
        Stop-Process -Id $mcpProcess.Id -Force
    }
    Remove-Item `
        -LiteralPath $McpPidFile `
        -Force `
        -ErrorAction SilentlyContinue
    throw
}

$workerScript = Join-Path $WorkDir "scripts\bid_intake_agent_worker.py"
$workerArgs = @(
    $workerScript,
    "--pid-file",
    $WorkerPidFile
)
$workerLaunchStartedAt = Get-Date
$workerProcess = Start-Process `
    -FilePath $AgentPython `
    -ArgumentList $workerArgs `
    -WorkingDirectory $WorkDir `
    -RedirectStandardOutput $workerOutLog `
    -RedirectStandardError $workerErrLog `
    -WindowStyle Hidden `
    -PassThru
$actualWorkerProcess = $null
try {
    $actualWorkerProcess = Wait-WorkerPidFile `
        -PidFile $WorkerPidFile `
        -LaunchedAfter $workerLaunchStartedAt `
        -TimeoutSeconds $StartupTimeoutSeconds
    Start-Sleep -Seconds 2
    if ($actualWorkerProcess.HasExited) {
        throw "Worker exited after publishing its PID."
    }
} catch {
    if ($actualWorkerProcess -and -not $actualWorkerProcess.HasExited) {
        Stop-Process -Id $actualWorkerProcess.Id -Force
    }
    if ($workerProcess -and -not $workerProcess.HasExited) {
        Stop-Process -Id $workerProcess.Id -Force
    }
    Remove-Item `
        -LiteralPath $WorkerPidFile `
        -Force `
        -ErrorAction SilentlyContinue
    throw (
        "Bid-intake worker exited during startup. " +
        "See $workerErrLog"
    )
}

Write-Host (
    "[OK] Tender Evidence MCP pid=$mcpListenerProcessId " +
    "at http://127.0.0.1:$McpPort/mcp"
) -ForegroundColor Green
Write-Host (
    "[OK] Bid-intake Agent worker pid=$($actualWorkerProcess.Id)"
) -ForegroundColor Green
Write-Host "[INFO] Agent logs: $LogDir" -ForegroundColor Cyan

# Redirection creates asynchronous stream readers in Windows PowerShell.
# Dispose local handles and terminate only this launcher; the detached MCP
# and Worker children continue running for start_all.ps1.
$mcpProcess.Dispose()
$workerProcess.Dispose()
[Console]::Out.Flush()
[Console]::Error.Flush()
[Environment]::Exit(0)
