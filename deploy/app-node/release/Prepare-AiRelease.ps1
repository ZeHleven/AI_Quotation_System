[CmdletBinding()]
param(
    [string]$BaseUrl = "https://www.qskingship.com",
    [string]$Username = "admin",
    [string]$PythonPath = "",
    [string[]]$AdditionalBackendTests = @(),
    [int]$ChunkSizeMB = 24,
    [switch]$ApproveSensitiveTests,
    [switch]$ApproveAgentRelease,
    [switch]$ApproveMigration,
    [switch]$PlanOnly,
    [switch]$NoUpload,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Get-CommandOutput {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Push-Location $WorkingDirectory
    try {
        $output = @(& $FilePath @Arguments)
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
        }
        return $output
    }
    finally {
        Pop-Location
    }
}

function Get-SingleCommandOutput {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    $values = @(Get-CommandOutput $FilePath $Arguments $WorkingDirectory)
    if ($values.Count -ne 1) {
        throw "Expected one output line from $FilePath but received $($values.Count): $($values -join '; ')"
    }
    return [string]$values[0]
}

function Resolve-Python([string]$RepositoryRoot, [string]$RequestedPython) {
    if ($RequestedPython) {
        if (-not (Test-Path -LiteralPath $RequestedPython -PathType Leaf)) {
            throw "Python runtime not found: $RequestedPython"
        }
        return (Resolve-Path -LiteralPath $RequestedPython).Path
    }
    $candidates = @(
        (Join-Path $RepositoryRoot "AI_Middle_Office\.venv\Scripts\python.exe"),
        (Join-Path $RepositoryRoot ".venv\Scripts\python.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\python.exe"),
        (Join-Path $env:USERPROFILE "anaconda3\python.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }
    throw "Python runtime not found. Pass -PythonPath with the Python used by the local test environment."
}

function Test-AnyPattern {
    param([string]$Path, [object[]]$Patterns)
    foreach ($pattern in $Patterns) {
        $matcher = New-Object System.Management.Automation.WildcardPattern(
            [string]$pattern,
            [System.Management.Automation.WildcardOptions]::IgnoreCase
        )
        if ($matcher.IsMatch($Path)) {
            return $true
        }
    }
    return $false
}

function Split-ReleaseArchive {
    param(
        [string]$ArchivePath,
        [string]$OutputDirectory,
        [int64]$ChunkBytes
    )
    $buffer = New-Object byte[] (1024 * 1024)
    $source = [System.IO.File]::OpenRead($ArchivePath)
    $parts = New-Object System.Collections.Generic.List[object]
    try {
        $index = 1
        while ($source.Position -lt $source.Length) {
            $partName = "{0}.part{1:D3}" -f ([System.IO.Path]::GetFileName($ArchivePath)), $index
            $partPath = Join-Path $OutputDirectory $partName
            $target = [System.IO.File]::Create($partPath)
            try {
                $remaining = [Math]::Min($ChunkBytes, $source.Length - $source.Position)
                while ($remaining -gt 0) {
                    $read = $source.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                    if ($read -le 0) {
                        throw "Unexpected end of archive while creating $partName"
                    }
                    $target.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            }
            finally {
                $target.Dispose()
            }
            $item = Get-Item -LiteralPath $partPath
            $parts.Add([ordered]@{
                filename = $item.Name
                size_bytes = [int64]$item.Length
                sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            })
            $index += 1
        }
    }
    finally {
        $source.Dispose()
    }
    return $parts.ToArray()
}

function New-ReleaseHttpClient {
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(30)
    return $client
}

function Get-PlainTextPassword([System.Security.SecureString]$SecurePassword) {
    return (New-Object System.Net.NetworkCredential("", $SecurePassword)).Password
}

function Invoke-ReleaseLogin {
    param(
        [System.Net.Http.HttpClient]$Client,
        [string]$ResolvedBaseUrl,
        [string]$LoginUsername
    )
    $securePassword = Read-Host "Password for $LoginUsername" -AsSecureString
    $plainPassword = Get-PlainTextPassword $securePassword
    try {
        $values = New-Object 'System.Collections.Generic.Dictionary[string,string]'
        $values.Add("username", $LoginUsername)
        $values.Add("password", $plainPassword)
        $body = New-Object System.Net.Http.FormUrlEncodedContent($values)
        try {
            $response = $Client.PostAsync("$ResolvedBaseUrl/api/v1/auth/login", $body).Result
            $payloadText = $response.Content.ReadAsStringAsync().Result
            if (-not $response.IsSuccessStatusCode) {
                throw "Login failed: HTTP $([int]$response.StatusCode) $payloadText"
            }
            $payload = $payloadText | ConvertFrom-Json
            $token = $payload.data.access_token
            if (-not $token) {
                $token = $payload.access_token
            }
            if (-not $token) {
                throw "Login response did not contain an access token."
            }
            return [string]$token
        }
        finally {
            $body.Dispose()
        }
    }
    finally {
        $plainPassword = $null
        $securePassword.Dispose()
    }
}

function Invoke-AuthenticatedJsonGet {
    param(
        [System.Net.Http.HttpClient]$Client,
        [string]$Url,
        [string]$Token
    )
    $request = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, $Url)
    $request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", $Token)
    try {
        $response = $Client.SendAsync($request).Result
        $payloadText = $response.Content.ReadAsStringAsync().Result
        if (-not $response.IsSuccessStatusCode) {
            throw "GET failed: HTTP $([int]$response.StatusCode) $payloadText"
        }
        return ($payloadText | ConvertFrom-Json)
    }
    finally {
        $request.Dispose()
    }
}

function Send-ReleaseFile {
    param(
        [System.Net.Http.HttpClient]$Client,
        [string]$ResolvedBaseUrl,
        [string]$Token,
        [string]$Purpose,
        [string]$FilePath
    )
    $file = Get-Item -LiteralPath $FilePath
    $multipart = New-Object System.Net.Http.MultipartFormDataContent
    $multipart.Add((New-Object System.Net.Http.StringContent($Purpose)), "purpose")
    $stream = [System.IO.File]::OpenRead($file.FullName)
    $streamContent = New-Object System.Net.Http.StreamContent($stream)
    $streamContent.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue("application/octet-stream")
    $multipart.Add($streamContent, "file", $file.Name)
    $request = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, "$ResolvedBaseUrl/api/v1/files")
    $request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", $Token)
    $request.Content = $multipart
    try {
        $response = $Client.SendAsync($request).Result
        $payloadText = $response.Content.ReadAsStringAsync().Result
        if (-not $response.IsSuccessStatusCode) {
            throw "Upload failed for $($file.Name): HTTP $([int]$response.StatusCode) $payloadText"
        }
        $payload = $payloadText | ConvertFrom-Json
        if ($payload.code -ne 200) {
            throw "Upload failed for $($file.Name): $payloadText"
        }
        Write-Host "Uploaded $($file.Name) ($($file.Length) bytes)"
    }
    finally {
        $request.Dispose()
    }
}

$releaseDirectory = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $releaseDirectory "..\..\..")).Path
$backendRoot = Join-Path $repositoryRoot "AI_Middle_Office"
$frontendRoot = Join-Path $repositoryRoot "ai-web"
$repositoryBaselinePath = Join-Path $releaseDirectory "production-baseline.json"
$testMapPath = Join-Path $releaseDirectory "test-map.json"

if ($ChunkSizeMB -lt 5 -or $ChunkSizeMB -gt 45) {
    throw "ChunkSizeMB must be between 5 and 45."
}

$commonGitDirectory = (Get-SingleCommandOutput git @("rev-parse", "--git-common-dir") $repositoryRoot).Trim()
if (-not [System.IO.Path]::IsPathRooted($commonGitDirectory)) {
    $commonGitDirectory = (Resolve-Path -LiteralPath (Join-Path $repositoryRoot $commonGitDirectory)).Path
}
$localBaselinePath = Join-Path $commonGitDirectory "ai-release-production-baseline.json"
$baselinePath = if (Test-Path -LiteralPath $localBaselinePath -PathType Leaf) {
    $localBaselinePath
}
else {
    $repositoryBaselinePath
}
$baseline = Get-Content -LiteralPath $baselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
$testMap = Get-Content -LiteralPath $testMapPath -Raw -Encoding UTF8 | ConvertFrom-Json
$headCommit = (Get-SingleCommandOutput git @("rev-parse", "HEAD") $repositoryRoot).Trim()
$shortCommit = (Get-SingleCommandOutput git @("rev-parse", "--short=7", "HEAD") $repositoryRoot).Trim()
$branch = (Get-SingleCommandOutput git @("branch", "--show-current") $repositoryRoot).Trim()

Invoke-Checked git @("merge-base", "--is-ancestor", [string]$baseline.production_commit, $headCommit) $repositoryRoot

$dirty = @((Get-CommandOutput git @("status", "--porcelain") $repositoryRoot) | Where-Object { $_ })
if ($dirty.Count -gt 0 -and -not $AllowDirty) {
    throw "Release worktree is not clean. Commit intentional changes before packaging."
}

$changedFiles = @(
    (Get-CommandOutput git @("diff", "--name-only", "$($baseline.production_commit)..$headCommit") $repositoryRoot) |
        ForEach-Object { ([string]$_).Trim().Replace("\", "/") } |
        Where-Object { $_ }
)
if ($AllowDirty) {
    $changedFiles += @(
        (Get-CommandOutput git @("status", "--porcelain") $repositoryRoot) |
            ForEach-Object {
                $line = [string]$_
                if ($line.Length -gt 3) { $line.Substring(3).Trim().Replace("\", "/") }
            } |
            Where-Object { $_ }
    )
    $changedFiles = @($changedFiles | Sort-Object -Unique)
}
if ($changedFiles.Count -eq 0) {
    throw "No changes were found after the recorded production commit."
}

$agentPatterns = @(
    "AI_Middle_Office/app/api/v1/bid_assessment*.py",
    "AI_Middle_Office/app/models/bid_assessment*.py",
    "AI_Middle_Office/app/services/bid_*.py",
    "AI_Middle_Office/app/tasks/bid_assessment*.py",
    "AI_Middle_Office/app/schemas/bid_assessment*.py",
    "AI_Middle_Office/contracts/bid_assessment/**",
    "AI_Middle_Office/schemas/bid_assessment/**",
    "AI_Middle_Office/openapi/bid-assessment*.json",
    "AI_Middle_Office/tests/test_bid_assessment*.py",
    "AI_Middle_Office/alembic/versions/202608*_bid_*.py",
    "ai-web/src/**/*bid-assessment*",
    "ai-web/src/**/*BidAssessment*"
)
$visionPatterns = @(
    "AI_Middle_Office/app/services/*ocr*.py",
    "AI_Middle_Office/app/services/drawing_*.py",
    "AI_Middle_Office/app/services/dxf_*.py",
    "AI_Middle_Office/scripts/*ocr*.py",
    "AI_Middle_Office/scripts/*drawing*.py",
    "AI_Middle_Office/scripts/*dxf*.py"
)
$agentChanges = @($changedFiles | Where-Object { Test-AnyPattern $_ $agentPatterns })
$visionChanges = @($changedFiles | Where-Object { Test-AnyPattern $_ $visionPatterns })
$migrationChanges = @($changedFiles | Where-Object { $_ -like "AI_Middle_Office/alembic/versions/*.py" })
$agentMarkerDiff = @(
    Get-CommandOutput git @(
        "diff", "--unified=0", "$($baseline.production_commit)..$headCommit", "--",
        "AI_Middle_Office/app", "AI_Middle_Office/alembic", "ai-web/src"
    ) $repositoryRoot
)
if (($agentMarkerDiff -join "`n") -match '(?i)bid[_-]assessment') {
    $agentChanges += "__diff_marker__:bid_assessment"
}
$agentChanges = @($agentChanges | Sort-Object -Unique)

if ($agentChanges.Count -gt 0 -and -not $ApproveAgentRelease) {
    throw "Bid-assessment Agent changes detected. This release is blocked until the user explicitly approves an Agent release.`n$($agentChanges -join "`n")"
}
if (($agentChanges.Count -gt 0 -or $visionChanges.Count -gt 0) -and -not $ApproveSensitiveTests) {
    throw "Image/OCR/Agent-sensitive changes detected. Ask the user whether to run those tests, then rerun with -ApproveSensitiveTests."
}
if ($migrationChanges.Count -gt 0 -and -not $ApproveMigration) {
    throw "Database migrations detected. A migration release needs explicit approval; rerun with -ApproveMigration only after approval."
}

$selectedTests = New-Object System.Collections.Generic.HashSet[string]([System.StringComparer]::OrdinalIgnoreCase)
$matchedBackendFiles = New-Object System.Collections.Generic.HashSet[string]([System.StringComparer]::OrdinalIgnoreCase)
foreach ($rule in @($testMap.rules)) {
    $matched = @($changedFiles | Where-Object { Test-AnyPattern $_ @($rule.patterns) })
    if ($matched.Count -gt 0) {
        foreach ($test in @($rule.tests)) {
            [void]$selectedTests.Add([string]$test)
        }
        foreach ($path in $matched) {
            [void]$matchedBackendFiles.Add([string]$path)
        }
    }
}
foreach ($test in $AdditionalBackendTests) {
    $normalizedTest = $test.Replace("\", "/")
    if (-not $normalizedTest.StartsWith("tests/") -or $normalizedTest.Contains("..")) {
        throw "Additional backend tests must stay below AI_Middle_Office/tests: $test"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $backendRoot $normalizedTest) -PathType Leaf)) {
        throw "Additional backend test not found: $test"
    }
    [void]$selectedTests.Add($normalizedTest)
}

$backendAppChanges = @(
    $changedFiles | Where-Object {
        $_ -like "AI_Middle_Office/app/*.py" -or
        $_ -like "AI_Middle_Office/app/**/*.py" -or
        $_ -like "AI_Middle_Office/alembic/*.py" -or
        $_ -like "AI_Middle_Office/alembic/**/*.py"
    }
)
$unmappedBackend = @(
    $backendAppChanges | Where-Object {
        -not $matchedBackendFiles.Contains($_) -and
        $_ -notlike "AI_Middle_Office/alembic/versions/*.py"
    }
)
if ($unmappedBackend.Count -gt 0 -and $AdditionalBackendTests.Count -eq 0) {
    throw "Backend changes have no focused test mapping. Add a rule to test-map.json or pass -AdditionalBackendTests.`n$($unmappedBackend -join "`n")"
}

$frontendChanged = @(
    $changedFiles | Where-Object {
        $_ -like "ai-web/*" -or $_ -like "ai-web/**" -or
        $_ -in @("app.html", "index.html", "admin.html") -or
        $_ -like "static/*" -or $_ -like "static/**"
    }
).Count -gt 0

$python = Resolve-Python $repositoryRoot $PythonPath
$alembicOutput = @(Get-CommandOutput $python @("-m", "alembic", "heads") $backendRoot)
$headLines = @($alembicOutput | Where-Object { ([string]$_) -match '^([^ ]+) \(head\)$' })
if ($headLines.Count -ne 1) {
    throw "Alembic must have exactly one head. Output: $($alembicOutput -join '; ')"
}
$targetDatabaseHead = ([regex]::Match([string]$headLines[0], '^([^ ]+)').Groups[1].Value)
$hasMigration = $targetDatabaseHead -ne [string]$baseline.database_head
if ($hasMigration -and -not $ApproveMigration) {
    throw "Database head changes from $($baseline.database_head) to $targetDatabaseHead. Explicit migration approval is required."
}
if ($hasMigration -and -not $PlanOnly) {
    throw "The reusable release command intentionally handles no-migration releases only. Use the dedicated backup, restore-drill and migration runbook."
}

$agentRuntimeAllowed = [bool]$baseline.agent_runtime_deployed -or $agentChanges.Count -gt 0

$testArguments = @($selectedTests | Sort-Object)
$plan = [ordered]@{
    baseline_commit = [string]$baseline.production_commit
    head_commit = $headCommit
    branch = $branch
    changed_file_count = $changedFiles.Count
    backend_tests = $testArguments
    frontend_build = $frontendChanged
    contains_agent_changes = $agentChanges.Count -gt 0
    agent_runtime_allowed = $agentRuntimeAllowed
    contains_vision_changes = $visionChanges.Count -gt 0
    contains_migration = $hasMigration
    database_from_head = [string]$baseline.database_head
    database_target_head = $targetDatabaseHead
}
Write-Step "Release plan"
$plan | ConvertTo-Json -Depth 8
if ($PlanOnly) {
    Write-Host "PASS|release_plan_only"
    return
}

Write-Step "Focused local validation"
Invoke-Checked $python @("-m", "compileall", "-q", "app") $backendRoot
if ($testArguments.Count -gt 0) {
    Invoke-Checked $python (@("-m", "pytest", "-q") + $testArguments) $backendRoot
}
if ($frontendChanged) {
    Invoke-Checked "npm.cmd" @("ci", "--no-audit", "--no-fund") $frontendRoot
    Invoke-Checked "npm.cmd" @("run", "build") $frontendRoot
}

$now = [DateTime]::UtcNow
$releaseId = "rel-{0}-{1}-{2}" -f $now.ToString("yyyyMMdd"), $now.ToString("HHmmss"), $shortCommit
$imageTag = "{0}-{1}-{2}" -f $now.ToString("yyyyMMdd"), $now.ToString("HHmmss"), $shortCommit
$imageRef = "ai-middle-office-app:$imageTag"
$bundleRoot = Join-Path $repositoryRoot ".tmp\release-bundles\$releaseId"
if (Test-Path -LiteralPath $bundleRoot) {
    throw "Release bundle already exists: $bundleRoot"
}
[void](New-Item -ItemType Directory -Path $bundleRoot -Force)

Write-Step "Build immutable application image"
Invoke-Checked "docker" @(
    "build",
    "--file", "deploy/app-node/Dockerfile",
    "--build-arg", "APP_VERSION=$imageTag",
    "--tag", $imageRef,
    "."
) $repositoryRoot

$imageId = (Get-SingleCommandOutput "docker" @("image", "inspect", $imageRef, "--format", "{{.Id}}") $repositoryRoot).Trim()
$imageUser = (Get-SingleCommandOutput "docker" @("image", "inspect", $imageRef, "--format", "{{.Config.User}}") $repositoryRoot).Trim()
$imageLabelsText = (Get-SingleCommandOutput "docker" @("image", "inspect", $imageRef, "--format", "{{json .Config.Labels}}") $repositoryRoot).Trim()
$imageLabels = $imageLabelsText | ConvertFrom-Json
$imageVersion = [string]$imageLabels.'org.opencontainers.image.version'
if ($imageUser -ne "10001:10001" -or $imageVersion -ne $imageTag -or -not $imageId.StartsWith("sha256:")) {
    throw "Built image metadata is invalid: id=$imageId user=$imageUser version=$imageVersion"
}

$archiveName = "ai-middle-office-app-$imageTag.tar"
$archivePath = Join-Path $bundleRoot $archiveName
Invoke-Checked "docker" @("save", "--output", $archivePath, $imageRef) $repositoryRoot
$archive = Get-Item -LiteralPath $archivePath
$archiveSha = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$parts = Split-ReleaseArchive $archivePath $bundleRoot ([int64]$ChunkSizeMB * 1024 * 1024)

$manifest = [ordered]@{
    schema_version = 1
    release_id = $releaseId
    created_at_utc = $now.ToString("yyyy-MM-ddTHH:mm:ssZ")
    source = [ordered]@{
        commit = $headCommit
        short_commit = $shortCommit
        branch = $branch
        baseline_commit = [string]$baseline.production_commit
        changed_files = $changedFiles
    }
    gates = [ordered]@{
        contains_agent_changes = $agentChanges.Count -gt 0
        agent_release_approved = [bool]$ApproveAgentRelease
        agent_runtime_allowed = $agentRuntimeAllowed
        contains_vision_changes = $visionChanges.Count -gt 0
        sensitive_tests_approved = [bool]$ApproveSensitiveTests
        contains_migration = $hasMigration
        migration_approved = [bool]$ApproveMigration
        focused_backend_tests = $testArguments
        frontend_build = $frontendChanged
    }
    database = [ordered]@{
        from_head = [string]$baseline.database_head
        target_head = $targetDatabaseHead
    }
    image = [ordered]@{
        reference = $imageRef
        tag = $imageTag
        id = $imageId
        user = $imageUser
        version = $imageVersion
        archive_filename = $archiveName
        archive_size_bytes = [int64]$archive.Length
        archive_sha256 = $archiveSha
    }
    transfer = [ordered]@{
        purpose = "release-transfer-$releaseId"
        chunk_size_bytes = [int64]$ChunkSizeMB * 1024 * 1024
        parts = $parts
    }
}
$manifestPath = Join-Path $bundleRoot "release-manifest.json"
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$manifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()

Write-Host "PASS|release_bundle=$bundleRoot|image=$imageRef|sha256=$archiveSha|parts=$($parts.Count)"
if ($NoUpload) {
    Write-Host "PASS|release_no_upload|manifest_sha256=$manifestSha"
    return
}

Write-Step "Upload release bundle through HTTPS"
$resolvedBaseUrl = $BaseUrl.TrimEnd("/")
$client = New-ReleaseHttpClient
try {
    $token = Invoke-ReleaseLogin $client $resolvedBaseUrl $Username
    $me = Invoke-AuthenticatedJsonGet $client "$resolvedBaseUrl/api/v1/auth/me" $token
    $roles = @($me.data.roles)
    if ($roles -notcontains "system_admin" -and $roles -notcontains "admin") {
        throw "The upload account must have system_admin or admin role."
    }
    $manifest.upload = [ordered]@{ username = [string]$me.data.username; roles = $roles }
    $manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $manifestSha = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()

    $purpose = [string]$manifest.transfer.purpose
    $existing = Invoke-AuthenticatedJsonGet $client "$resolvedBaseUrl/api/v1/files?purpose=$purpose&page=1&page_size=100" $token
    if ([int]$existing.total -ne 0) {
        throw "The release purpose already has $($existing.total) uploaded files. Use a new release ID or purge the incomplete transfer on ECS."
    }
    foreach ($part in $parts) {
        Send-ReleaseFile $client $resolvedBaseUrl $token $purpose (Join-Path $bundleRoot $part.filename)
    }
    Send-ReleaseFile $client $resolvedBaseUrl $token $purpose $manifestPath
}
finally {
    if ($client) { $client.Dispose() }
}

Write-Host "PASS|release_uploaded=$releaseId|manifest_sha256=$manifestSha"
Write-Host "NEXT|Open ECS SSM and run: sudo /usr/local/sbin/ai-release deploy $releaseId"
