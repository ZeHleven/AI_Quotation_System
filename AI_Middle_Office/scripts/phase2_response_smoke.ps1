param(
    [string]$BaseUrl = "http://127.0.0.1:9000/api/v1",
    [string]$Username = "admin",
    [int]$MinutesAgo = 15,
    [int]$PollCount = 20,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = "Stop"

$password = Read-Host "Enter password for $Username"

$login = Invoke-RestMethod `
    -Uri "$BaseUrl/auth/login" `
    -Method Post `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{ username = $Username; password = $password }

$headers = @{ Authorization = "Bearer $($login.access_token)" }

$chinaTz = [TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
$chinaNow = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $chinaTz)
$inquiryTime = $chinaNow.DateTime.AddMinutes(-1 * [Math]::Abs($MinutesAgo)).ToString("yyyy-MM-dd HH:mm:ss")

$job = Invoke-RestMethod `
    -Uri "$BaseUrl/quote/jobs" `
    -Method Post `
    -Headers $headers `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{
        message = "Phase 2 response speed smoke: living room floor tile 20 sqm, include material and labor budget."
        source = "manual"
        client_name = "Phase2 smoke client"
        client_phone = "13800000000"
        inquiry_time = $inquiryTime
        time_source = "manual"
        notes = "Phase2 response dashboard smoke"
    }

$jobId = $job.data.job_id
Write-Host "created job_id=$jobId client_inquiry_id=$($job.data.client_inquiry_id) inquiry_time=$inquiryTime"

for ($i = 0; $i -lt $PollCount; $i++) {
    Start-Sleep -Seconds $PollSeconds
    $latest = Invoke-RestMethod -Uri "$BaseUrl/quote/jobs/$jobId" -Headers $headers
    Write-Host ("{0} status={1} stage={2}" -f (Get-Date -Format "HH:mm:ss"), $latest.data.status, $latest.data.stage)
    if ($latest.data.status -notin @("queued", "processing", "running")) {
        break
    }
}

$dashboard = Invoke-RestMethod -Uri "$BaseUrl/admin/dashboard/response-speed?range=today" -Headers $headers
$dashboard.data |
    Select-Object sample_count_total, sample_count_in_avg, avg_first_response_minutes, sla_pass_rate, empty_state, low_sample_warning |
    Format-List
