param(
    [string]$BaseUrl = "http://127.0.0.1:9000/api/v1",
    [string]$PythonPath = "C:\Users\12521\miniconda3\python.exe"
)

$ErrorActionPreference = "Stop"

function Invoke-Json {
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
    }
    if ($null -ne $Body) {
        if ($ContentType -eq "application/json") {
            $params.ContentType = "application/json; charset=utf-8"
            $jsonBody = $Body | ConvertTo-Json -Depth 8
            $params.Body = [System.Text.Encoding]::UTF8.GetBytes($jsonBody)
        } else {
            $params.ContentType = $ContentType
            $params.Body = $Body
        }
    }
    Invoke-RestMethod @params
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

if (-not (Test-Path $PythonPath)) {
    throw "Python not found: $PythonPath"
}

$healthUrl = $BaseUrl -replace "/api/v1$", "/health/ready"
$health = Invoke-Json -Uri $healthUrl
if ($health.status -ne "ready") {
    throw "FastAPI is not ready: $($health.status)"
}

$password = "Phase4a!" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$env:PHASE4A_SMOKE_PASSWORD = $password

$createUsersCode = @'
import json
import os
import sys
import uuid

sys.path.insert(0, os.getcwd())

from app.core.database import SessionLocal
from app.core.security import get_password_hash
from app.models.user import User, UserRole

password = os.environ["PHASE4A_SMOKE_PASSWORD"]
suffix = uuid.uuid4().hex[:8]
users = {}

db = SessionLocal()
try:
    for role, legacy_role in (("admin", "admin"), ("staff", "user")):
        username = f"phase4a_smoke_{role}_{suffix}"
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
        db.add(UserRole(user_id=user.id, role=role, created_by=None, note="phase4a_runtime_smoke"))
        users[role] = {"id": user.id, "username": username}
    db.commit()
    print(json.dumps(users, ensure_ascii=False))
finally:
    db.close()
'@

$tempCreateUsersScript = Join-Path ([System.IO.Path]::GetTempPath()) ("phase4a_create_users_{0}.py" -f [guid]::NewGuid().ToString("N"))
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

$featureCheck = Invoke-Json `
    -Uri "$BaseUrl/meetings" `
    -Headers $staffHeaders
if ($null -eq $featureCheck.data) {
    throw "Meeting API did not return a paged response. Check FEATURE_MEETING_AI."
}

$dueTomorrow = (Get-Date).AddDays(1).ToString("yyyy-MM-ddTHH:mm:ss")
$dueAfterTomorrow = (Get-Date).AddDays(2).ToString("yyyy-MM-ddTHH:mm:ss")
$extractContent = "$(Join-CodePoints @(0x8BF7)) $($users.staff.username) $(Join-CodePoints @(0x660E,0x5929,0x8D1F,0x8D23,0x590D,0x6838,0x53A8,0x623F,0x5C3A,0x5BF8,0x3002))"
$noTaskContent = Join-CodePoints @(0x4ECA,0x5929,0x53EA,0x540C,0x6B65,0x5BA2,0x6237,0x80CC,0x666F,0xFF0C,0x6CA1,0x6709,0x5F62,0x6210,0x660E,0x786E,0x5F85,0x529E,0x3002)
$revisionInitialContent = "$(Join-CodePoints @(0x8BF7)) $($users.staff.username) $(Join-CodePoints @(0x660E,0x5929,0x8D1F,0x8D23,0x6574,0x7406,0x62A5,0x4EF7,0x590D,0x76D8,0x3002))"
$revisionSupplementalContent = "$(Join-CodePoints @(0x8865,0x5145,0xFF1A,0x8BF7)) $($users.staff.username) $(Join-CodePoints @(0x540E,0x5929,0x8D1F,0x8D23,0x63D0,0x4EA4,0x5BA2,0x6237,0x56DE,0x8BBF,0x7ED3,0x679C,0x3002))"

$meeting = Invoke-Json `
    -Uri "$BaseUrl/meetings" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{ content = $extractContent }

$draft = $meeting.data.drafts | Select-Object -First 1
if ($meeting.data.ai_status -ne "extracted" -or $null -eq $draft) {
    throw "Expected extracted task draft, got ai_status=$($meeting.data.ai_status)"
}

$confirmed = Invoke-Json `
    -Uri "$BaseUrl/meetings/$($meeting.data.id)/confirm-tasks" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{
        drafts = @(
            @{
                draft_id = $draft.id
                action = "accept"
                title = "Phase4a smoke - review kitchen measurements"
                assignee_id = $users.staff.id
                due_at = $dueTomorrow
                notes = "Runtime smoke confirms meeting draft into execution task."
            }
        )
    }

if ($confirmed.data.meeting.status -ne "confirmed" -or $confirmed.data.tasks.Count -lt 1) {
    throw "Confirm path failed"
}

$manualMeeting = Invoke-Json `
    -Uri "$BaseUrl/meetings" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{ content = $noTaskContent }

if ($manualMeeting.data.ai_status -ne "no_tasks") {
    throw "Expected no_tasks meeting for manual fallback"
}

$manualDraft = Invoke-Json `
    -Uri "$BaseUrl/meetings/$($manualMeeting.data.id)/drafts" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{
        title = "Phase4a smoke - manual follow-up"
        source_sentence = "Manual fallback draft from runtime smoke."
        assignee_id = $users.staff.id
        due_at = $dueAfterTomorrow
        notes = "AI extraction produced no tasks."
    }

$cancelled = Invoke-Json `
    -Uri "$BaseUrl/meetings/$($manualMeeting.data.id)/cancel" `
    -Method Post `
    -Headers $staffHeaders `
    -Body @{ reason = "Phase4a runtime smoke cancellation check" }

if ($cancelled.data.status -ne "cancelled") {
    throw "Cancel path failed"
}

$revisionMeeting = Invoke-Json `
    -Uri "$BaseUrl/meetings" `
    -Method Post `
    -Headers $adminHeaders `
    -Body @{ content = $revisionInitialContent }

$revisionDraft = $revisionMeeting.data.drafts | Select-Object -First 1
$initialConfirm = Invoke-Json `
    -Uri "$BaseUrl/meetings/$($revisionMeeting.data.id)/confirm-tasks" `
    -Method Post `
    -Headers $adminHeaders `
    -Body @{
        drafts = @(
            @{
                draft_id = $revisionDraft.id
                action = "accept"
                assignee_id = $users.staff.id
                due_at = $dueTomorrow
            }
        )
    }

if ($initialConfirm.data.meeting.status -ne "confirmed") {
    throw "Initial revision meeting confirm failed"
}

$revised = Invoke-Json `
    -Uri "$BaseUrl/meetings/$($revisionMeeting.data.id)/revisions" `
    -Method Post `
    -Headers $adminHeaders `
    -Body @{
        content = $revisionSupplementalContent
        reason = "Phase4a runtime smoke supplemental task"
    }

$supplementalDraft = $revised.data.drafts |
    Where-Object { $_.status -eq "pending_review" -and $null -ne $_.revision_id } |
    Select-Object -First 1
if ($revised.data.status -ne "revised" -or $null -eq $supplementalDraft) {
    throw "Revision did not create a supplemental draft"
}

$revisionConfirm = Invoke-Json `
    -Uri "$BaseUrl/meetings/$($revisionMeeting.data.id)/confirm-tasks" `
    -Method Post `
    -Headers $adminHeaders `
    -Body @{
        drafts = @(
            @{
                draft_id = $supplementalDraft.id
                action = "accept"
                assignee_id = $users.staff.id
                due_at = $dueAfterTomorrow
            }
        )
    }

if ($revisionConfirm.data.meeting.status -ne "confirmed" -or $revisionConfirm.data.tasks.Count -lt 1) {
    throw "Revision confirm path failed"
}

$executionPageUrl = ($BaseUrl -replace "/api/v1$", "/admin/execution")
$executionPage = Invoke-WebRequest -Uri $executionPageUrl -TimeoutSec 30 -UseBasicParsing
if ($executionPage.StatusCode -ne 200) {
    throw "Vite execution page returned HTTP $($executionPage.StatusCode)"
}

[PSCustomObject]@{
    status = "passed"
    feature_meeting_ai = "enabled"
    public_access_enabled = "false_expected"
    smoke_admin = $users.admin.username
    smoke_staff = $users.staff.username
    confirmed_meeting_id = $meeting.data.id
    confirmed_task_id = ($confirmed.data.tasks | Select-Object -First 1).id
    manual_cancelled_meeting_id = $manualMeeting.data.id
    manual_draft_id = $manualDraft.data.id
    revision_meeting_id = $revisionMeeting.data.id
    revision_task_id = ($revisionConfirm.data.tasks | Select-Object -First 1).id
    execution_page_status = $executionPage.StatusCode
} | Format-List
