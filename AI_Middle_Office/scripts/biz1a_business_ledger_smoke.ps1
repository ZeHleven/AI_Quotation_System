param(
    [string]$BaseUrl = "http://localhost:9000/api/v1",
    [string]$PythonPath = "C:\Users\12521\miniconda3\python.exe"
)

$ErrorActionPreference = "Stop"

function Invoke-Api {
    param(
        [string]$Uri,
        [string]$Method = "Get",
        [hashtable]$Headers = @{},
        [object]$Body = $null,
        [string]$ContentType = "application/json"
    )
    $params = @{
        Uri = $Uri
        Method = $Method
        Headers = $Headers
        TimeoutSec = 30
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        if ($ContentType -eq "application/json") {
            $params.ContentType = "application/json; charset=utf-8"
            $jsonBody = $Body | ConvertTo-Json -Depth 10
            $params.Body = [System.Text.Encoding]::UTF8.GetBytes($jsonBody)
        } else {
            $params.ContentType = $ContentType
            $params.Body = $Body
        }
    }

    try {
        $response = Invoke-WebRequest @params
        $content = $response.Content
        if ($response.RawContentStream) {
            $response.RawContentStream.Position = 0
            $reader = New-Object System.IO.StreamReader($response.RawContentStream, [System.Text.Encoding]::UTF8)
            $content = $reader.ReadToEnd()
        }
        $bodyObject = $null
        if ($content) {
            $bodyObject = $content | ConvertFrom-Json
        }
        return [PSCustomObject]@{
            Status = [int]$response.StatusCode
            Body = $bodyObject
            Raw = $content
        }
    } catch {
        $statusCode = $null
        $content = $null
        if ($_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
            try {
                $stream = $_.Exception.Response.GetResponseStream()
                if ($stream) {
                    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
                    $content = $reader.ReadToEnd()
                }
            } catch {
                $content = $null
            }
        }
        if (-not $content -and $_.ErrorDetails.Message) {
            $content = $_.ErrorDetails.Message
        }
        $bodyObject = $null
        if ($content) {
            try {
                $bodyObject = $content | ConvertFrom-Json
            } catch {
                $bodyObject = $null
            }
        }
        return [PSCustomObject]@{
            Status = $statusCode
            Body = $bodyObject
            Raw = $content
        }
    }
}

function Assert-Status {
    param(
        [object]$Response,
        [int]$ExpectedStatus,
        [string]$Label
    )
    if ($null -eq $Response.Status) {
        throw "$Label did not return an HTTP response. Check that FastAPI is running, ready, and not stuck during startup. Body: $($Response.Raw)"
    }
    if ($Response.Status -ne $ExpectedStatus) {
        throw "$Label returned HTTP $($Response.Status), expected $ExpectedStatus. Body: $($Response.Raw)"
    }
}

function Invoke-Json {
    param(
        [string]$Uri,
        [string]$Method = "Get",
        [hashtable]$Headers = @{},
        [object]$Body = $null,
        [string]$ContentType = "application/json",
        [int]$ExpectedStatus = 200
    )
    $response = Invoke-Api -Uri $Uri -Method $Method -Headers $Headers -Body $Body -ContentType $ContentType
    Assert-Status -Response $response -ExpectedStatus $ExpectedStatus -Label "$Method $Uri"
    return $response.Body
}

function Login-SmokeUser {
    param(
        [string]$Username,
        [string]$Password
    )
    $login = Invoke-Json `
        -Uri "$BaseUrl/auth/login" `
        -Method Post `
        -ContentType "application/x-www-form-urlencoded" `
        -Body @{ username = $Username; password = $Password }
    @{ Authorization = "Bearer $($login.access_token)" }
}

function Join-CodePoints {
    param([int[]]$Codes)
    -join ($Codes | ForEach-Object { [char]$_ })
}

function Get-PageItems {
    param([object]$ResponseBody)
    if ($null -eq $ResponseBody.data) {
        return @()
    }
    return @($ResponseBody.data)
}

function Assert-ContainsInquiry {
    param(
        [object[]]$Items,
        [string]$InquiryId,
        [string]$Label
    )
    $match = $Items | Where-Object { $_.inquiry_id -eq $InquiryId } | Select-Object -First 1
    if ($null -eq $match) {
        throw "$Label did not contain inquiry_id=$InquiryId"
    }
}

function Assert-NotContainsInquiry {
    param(
        [object[]]$Items,
        [string]$InquiryId,
        [string]$Label
    )
    $match = $Items | Where-Object { $_.inquiry_id -eq $InquiryId } | Select-Object -First 1
    if ($null -ne $match) {
        throw "$Label unexpectedly contained inquiry_id=$InquiryId"
    }
}

if (-not (Test-Path $PythonPath)) {
    throw "Python not found: $PythonPath"
}

$backendRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$healthUrl = $BaseUrl -replace "/api/v1$", "/health/ready"
$health = Invoke-Json -Uri $healthUrl
if ($health.status -ne "ready") {
    throw "FastAPI is not ready: $($health.status)"
}

$password = "Biz1a!" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$env:BIZ1A_SMOKE_PASSWORD = $password
$env:BIZ1A_BACKEND_ROOT = $backendRoot

$createUsersCode = @'
import json
import os
import sys
import uuid

sys.path.insert(0, os.environ["BIZ1A_BACKEND_ROOT"])

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole

password = os.environ["BIZ1A_SMOKE_PASSWORD"]
suffix = uuid.uuid4().hex[:8]
users = {}

db = SessionLocal()
try:
    for role, legacy_role in (("admin", "admin"), ("staff", "user")):
        username = f"biz1a_smoke_{role}_{suffix}"
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            role=legacy_role,
            role_version=1,
            quota=20,
            is_active=True,
            must_change_password=False,
        )
        db.add(user)
        db.flush()
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="biz1a_runtime_smoke"))
        users[role] = {"id": user.id, "username": username}
    db.commit()
    print(json.dumps(users, ensure_ascii=False))
finally:
    db.close()
'@

$tempCreateUsersScript = Join-Path ([System.IO.Path]::GetTempPath()) ("biz1a_create_users_{0}.py" -f [guid]::NewGuid().ToString("N"))
Set-Content -Path $tempCreateUsersScript -Value $createUsersCode -Encoding UTF8
try {
    $userJson = & $PythonPath $tempCreateUsersScript
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create smoke users"
    }
} finally {
    Remove-Item -LiteralPath $tempCreateUsersScript -Force -ErrorAction SilentlyContinue
}

$users = $userJson | ConvertFrom-Json
$adminHeaders = Login-SmokeUser -Username $users.admin.username -Password $password
$staffHeaders = Login-SmokeUser -Username $users.staff.username -Password $password

$featureCheck = Invoke-Api -Uri "$BaseUrl/business-ledger" -Headers $staffHeaders
if ($featureCheck.Status -eq 404 -and $featureCheck.Body.detail -eq "NOT_FOUND") {
    throw "FEATURE_BUSINESS_LEDGER is not enabled for $BaseUrl"
}
if ($featureCheck.Status -eq 404 -and $featureCheck.Body.detail -eq "Not Found") {
    throw "Business ledger route is not registered on $BaseUrl. Restart FastAPI with the current code before running this smoke."
}
Assert-Status -Response $featureCheck -ExpectedStatus 200 -Label "GET /business-ledger feature check"

$responseBefore = Invoke-Json -Uri "$BaseUrl/admin/dashboard/response-speed?range=last_30_days" -Headers $adminHeaders
$beforeTotal = [int]$responseBefore.data.sample_count_total
$beforeInAvg = [int]$responseBefore.data.sample_count_in_avg

$stageRequirementConfirmation = Join-CodePoints @(0x9700, 0x6C42, 0x786E, 0x8BA4)
$source = "biz1a-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
$pastFollowup = (Get-Date).AddDays(-1).ToString("yyyy-MM-ddTHH:mm:ss")
$futureFollowup = (Get-Date).AddDays(2).ToString("yyyy-MM-ddTHH:mm:ss")

$staffLedger = Invoke-Json `
    -Uri "$BaseUrl/business-ledger" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{
        source = $source
        client_name = "Smoke Staff Client"
        client_phone = "13900000001"
        next_followup_at = $pastFollowup
        notes = "Project: staff-owned smoke ledger. Company: smoke placeholder."
    }

$adminLedger = Invoke-Json `
    -Uri "$BaseUrl/business-ledger" `
    -Method Post `
    -Headers $adminHeaders `
    -Body @{
        source = $source
        client_name = "Smoke Admin Client"
        client_phone = "13900000002"
        responder_id = $users.admin.id
        next_followup_at = $futureFollowup
        notes = "Project: admin-owned smoke ledger."
    }

$staffItems = Get-PageItems (Invoke-Json -Uri "$BaseUrl/business-ledger?source=$source" -Headers $staffHeaders)
Assert-ContainsInquiry -Items $staffItems -InquiryId $staffLedger.data.inquiry_id -Label "staff ledger list"
Assert-NotContainsInquiry -Items $staffItems -InquiryId $adminLedger.data.inquiry_id -Label "staff ledger list"

$adminItems = Get-PageItems (Invoke-Json -Uri "$BaseUrl/business-ledger?source=$source" -Headers $adminHeaders)
Assert-ContainsInquiry -Items $adminItems -InquiryId $staffLedger.data.inquiry_id -Label "admin ledger list"
Assert-ContainsInquiry -Items $adminItems -InquiryId $adminLedger.data.inquiry_id -Label "admin ledger list"

$overdueItems = Get-PageItems (Invoke-Json -Uri "$BaseUrl/business-ledger?source=$source&overdue_only=true" -Headers $adminHeaders)
Assert-ContainsInquiry -Items $overdueItems -InquiryId $staffLedger.data.inquiry_id -Label "admin overdue ledger list"
Assert-NotContainsInquiry -Items $overdueItems -InquiryId $adminLedger.data.inquiry_id -Label "admin overdue ledger list"

$adminStaffDetail = Invoke-Json -Uri "$BaseUrl/business-ledger/$($staffLedger.data.inquiry_id)" -Headers $adminHeaders
if ($adminStaffDetail.data.inquiry_id -ne $staffLedger.data.inquiry_id) {
    throw "Admin detail did not return staff-created ledger"
}

$hiddenDetail = Invoke-Api -Uri "$BaseUrl/business-ledger/$($adminLedger.data.inquiry_id)" -Headers $staffHeaders
Assert-Status -Response $hiddenDetail -ExpectedStatus 404 -Label "staff detail hidden ledger"
if ($hiddenDetail.Body.detail -ne "BUSINESS_LEDGER_NOT_FOUND") {
    throw "Expected BUSINESS_LEDGER_NOT_FOUND, got $($hiddenDetail.Raw)"
}

$traceId = "biz1a-smoke-" + [guid]::NewGuid().ToString("N")
$staffPatchHeaders = $staffHeaders.Clone()
$staffPatchHeaders["X-Forwarded-For"] = "1.2.3.4, 5.6.7.8"
$staffPatchHeaders["X-Trace-Id"] = $traceId
$staffPatchHeaders["User-Agent"] = "BIZ1aSmoke/1.0"

$patched = Invoke-Json `
    -Uri "$BaseUrl/business-ledger/$($staffLedger.data.inquiry_id)" `
    -Method Patch `
    -Headers $staffPatchHeaders `
    -Body @{
        stage = $stageRequirementConfirmation
        client_phone = "13900000999"
        notes = "Project: staff smoke ledger updated through whitelist."
        next_followup_at = $futureFollowup
    }
if ($patched.data.stage -ne $stageRequirementConfirmation -or $patched.data.client_phone -ne "13900000999") {
    throw "Staff whitelist PATCH did not persist expected fields. stage=$($patched.data.stage); expected_stage=$stageRequirementConfirmation; client_phone=$($patched.data.client_phone)"
}

$staffForbiddenPatch = Invoke-Api `
    -Uri "$BaseUrl/business-ledger/$($staffLedger.data.inquiry_id)" `
    -Method Patch `
    -Headers $staffHeaders `
    -Body @{ source = "staff-should-not-edit-source" }
if ($staffForbiddenPatch.Status -notin @(403, 422)) {
    throw "Staff extended-field PATCH returned HTTP $($staffForbiddenPatch.Status), expected 403 or 422"
}

$cancelled = Invoke-Json `
    -Uri "$BaseUrl/business-ledger/$($adminLedger.data.inquiry_id)/cancel" `
    -Method Post `
    -Headers $adminHeaders `
    -Body @{ reason = "BIZ-1a runtime smoke cancellation" }
if (-not $cancelled.data.cancelled_at) {
    throw "Admin cancel did not set cancelled_at"
}

$staffCancel = Invoke-Api `
    -Uri "$BaseUrl/business-ledger/$($staffLedger.data.inquiry_id)/cancel" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{ reason = "staff should not cancel" }
Assert-Status -Response $staffCancel -ExpectedStatus 403 -Label "staff cancel"

$phase2List = Get-PageItems (Invoke-Json -Uri "$BaseUrl/client-inquiries?source=$source" -Headers $adminHeaders)
Assert-NotContainsInquiry -Items $phase2List -InquiryId $staffLedger.data.inquiry_id -Label "Phase 2 client-inquiries list"
Assert-NotContainsInquiry -Items $phase2List -InquiryId $adminLedger.data.inquiry_id -Label "Phase 2 client-inquiries list"

$responseAfter = Invoke-Json -Uri "$BaseUrl/admin/dashboard/response-speed?range=last_30_days" -Headers $adminHeaders
$afterTotal = [int]$responseAfter.data.sample_count_total
$afterInAvg = [int]$responseAfter.data.sample_count_in_avg
if ($beforeTotal -ne $afterTotal -or $beforeInAvg -ne $afterInAvg) {
    throw "Response dashboard changed after outbound smoke records. Before total/avg=$beforeTotal/$beforeInAvg after=$afterTotal/$afterInAvg"
}

$env:BIZ1A_SMOKE_INQUIRY_IDS = "$($staffLedger.data.inquiry_id),$($adminLedger.data.inquiry_id)"
$env:BIZ1A_SMOKE_TRACE_ID = $traceId

$eventAuditCode = @'
import json
import os
import sys

sys.path.insert(0, os.environ["BIZ1A_BACKEND_ROOT"])

from app.core.database import SessionLocal
from app.models.client_inquiry import ClientInquiry  # noqa: F401
from app.models.client_inquiry_event import ClientInquiryEvent

inquiry_ids = [item for item in os.environ["BIZ1A_SMOKE_INQUIRY_IDS"].split(",") if item]
trace_id = os.environ["BIZ1A_SMOKE_TRACE_ID"]

db = SessionLocal()
try:
    events = (
        db.query(ClientInquiryEvent)
        .filter(ClientInquiryEvent.inquiry_id.in_(inquiry_ids))
        .order_by(ClientInquiryEvent.id.asc())
        .all()
    )
    trace_event = next((event for event in events if event.trace_id == trace_id), None)
    if trace_event is None:
        raise SystemExit(f"No event found for trace_id={trace_id}")
    if trace_event.ip_address != "1.2.3.4":
        raise SystemExit(f"Expected XFF ip_address 1.2.3.4, got {trace_event.ip_address!r}")
    payload = {
        "event_count": len(events),
        "cancel_count": sum(1 for event in events if event.event_type == "cancel"),
        "create_count": sum(1 for event in events if event.event_type == "create"),
        "xff_event": {
            "event_type": trace_event.event_type,
            "ip_address": trace_event.ip_address,
            "user_agent": trace_event.user_agent,
            "trace_id": trace_event.trace_id,
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
finally:
    db.close()
'@

$tempEventAuditScript = Join-Path ([System.IO.Path]::GetTempPath()) ("biz1a_event_audit_{0}.py" -f [guid]::NewGuid().ToString("N"))
Set-Content -Path $tempEventAuditScript -Value $eventAuditCode -Encoding UTF8
try {
    $eventAuditJson = & $PythonPath $tempEventAuditScript
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to verify business ledger events"
    }
} finally {
    Remove-Item -LiteralPath $tempEventAuditScript -Force -ErrorAction SilentlyContinue
}
$eventAudit = $eventAuditJson | ConvertFrom-Json

Write-Host "XFF audit event:"
$eventAudit.xff_event | Format-List
Write-Host "BIZ-1a smoke OK: created=2 cancelled=$($eventAudit.cancel_count) feature_flag=true"

[PSCustomObject]@{
    status = "passed"
    smoke_admin = $users.admin.username
    smoke_staff = $users.staff.username
    staff_ledger_id = $staffLedger.data.inquiry_id
    admin_ledger_id = $adminLedger.data.inquiry_id
    event_count = $eventAudit.event_count
    response_sample_count_total = $afterTotal
    response_sample_count_in_avg = $afterInAvg
} | Format-List
