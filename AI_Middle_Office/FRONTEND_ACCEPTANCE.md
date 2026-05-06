# Frontend Acceptance Checklist

> Purpose: this checklist is the baseline for frontend changes. Before extracting `shared.js`, splitting `admin.html`, or changing user-facing flows, run the affected sections and record the result.

## Execution Rules

- Run the checklist against a healthy local service: `http://localhost:9000/health/ready` returns `status=ready`.
- Use a valid admin account for admin-only sections and a normal user account for regular-user sections.
- Prefer read-only checks on production data. Items marked **writes data** should be run in a test environment, or only after confirming backup/rollback expectations.
- Each item must verify the expected result, not just that the page opens.
- For every frontend refactor, test the directly changed section plus:
  - A1 login success
  - A3 expired/invalid token handling
  - K1 common API error handling

## Result Legend

- `[ ]` Not run
- `[x]` Passed
- `[!]` Failed or needs follow-up
- `[-]` Skipped with reason

---

## A. Login And Auth

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | A1 Login success | Open `app.html`, enter a valid username and password, click login. | Browser navigates to `index.html`; `localStorage` contains token and user info; current user is shown after page load; refresh keeps the session. | Covers token write and post-login redirect. |
| [ ] | A2 Wrong password | Open `app.html`, enter a valid username with a wrong password. | Stays on `app.html`; shows a readable login failure; no token is written to `localStorage`. | Must not leave stale user state. |
| [ ] | A3 Expired or invalid token | Manually clear or corrupt token, then open `index.html` and `admin.html`. | Redirects to `app.html`; page does not stay blank; stale user/admin state is cleared. | Required after shared auth changes. |
| [ ] | A4 Logout | Log in, click logout from the workbench. | Token and user info are removed; browser returns to `app.html`; Back/refresh does not re-enter authenticated pages. | Check both `index.html` and `admin.html` if present. |
| [ ] | A5 Must-change-password flow | Use an account flagged `must_change_password=true`. | Login opens the password-change dialog; weak/short password is rejected; successful change allows navigation; old password no longer works. | Run only when such an account is available. |

## B. User Quote Workbench

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | B1 Text quote submission | On `index.html`, enter a normal text quote request and submit. | A quote job is created; progress area appears with `创建任务 → 需求解析 → 知识库检索 → 生成报价 → 人工预审`; current status text and elapsed seconds update from SSE events; pre-review dialog opens only after line items are parsed and the table is not blank. | Verifies quote job API + SSE. |
| [ ] | B2 Empty request validation | Submit with no text and no file. | Frontend shows a readable validation message; no assistant bubble, loading spinner, timer, or stuck progress state remains. | No backend job should be created. |
| [ ] | B3 File upload quote | Upload a supported image/file and submit a quote request. | Upload/processing state is visible; progress uses `图像识别` instead of `需求解析`; GLM/file-load failures show a readable error state with retry available; success renders quote result. | If MinIO is enabled, also checks file reference path. |
| [ ] | B4 Long-running progress | Submit a request that takes long enough to observe multiple stages. | Progress/phase labels keep updating; elapsed seconds increase; long `RAG/n8n` waits advance to `生成报价`; user can tell the task is still running; no duplicate final result is shown. | Important for user trust. |
| [ ] | B5 Failed quote retry | Trigger or use a failed quote task, then use the recovery buttons in the failed assistant bubble. | The bubble shows task id/trace id when available; `查看任务状态` can recover a completed result into the pre-review dialog; `重新提交` reuses the failed text/file and creates a new task without the previous error blocking the run. | **writes data**: creates quote jobs. |
| [ ] | B6 Confirm push | After a successful quote, click confirm/push. | Confirm dialog data is correct; push result is readable; successful push writes history and displays the success state. | **writes data** and may push to DingTalk/N8N. Use test channel when possible. |

## C. History Drawer

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | C1 Open history | From `index.html`, open the history drawer. | Drawer opens without layout jump; first page loads; empty state is readable if no data exists. | Covers `api_page` parsing. |
| [ ] | C2 Pagination | Change page or page size in history. | List updates to the selected page; pagination total/current page are correct; no duplicate rows appear. | |
| [ ] | C3 Detail view | Open a history item detail. | Detail renders item content/amounts without `[object Object]` or broken JSON; close returns to list state. | |
| [ ] | C4 User isolation | Log in as a normal user and open history. | User only sees their own history; admin-only filters are not exposed. | Requires normal-user account. |

## D. Admin Access And User Quota

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | D1 Non-admin blocked | Log in as normal user and open `admin.html`. | Access is denied or redirected; admin data is not rendered. | |
| [ ] | D2 Admin dashboard loads | Log in as admin and open `admin.html`. | Admin page loads all main panels without blank areas or console-blocking errors. | Check top-level layout. |
| [ ] | D3 User list | Refresh the user/quota panel. | User table loads; role/quota/status columns are readable; pagination or refresh controls work. | |
| [ ] | D4 Update quota | Change a user's quota and save. | Success message appears; refreshed row shows the new quota; invalid quota is rejected with readable error. | **writes data**. Use a test user. |

## E. Materials Knowledge Base

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | E1 Materials list | Open the materials panel. | List renders current materials; count matches backend response; loading and empty states are readable. | Read-only. |
| [ ] | E2 Search/filter | Search for a known material keyword. | Results filter correctly; clearing the search restores the full list. | |
| [ ] | E3 Add material | Add a test material. | Validation catches missing required fields; valid submit adds the row; row remains after refresh. | **writes data**. Prefer test environment. |
| [ ] | E4 Edit material | Edit a test material. | Updated fields persist after refresh; unchanged fields are preserved. | **writes data**. |
| [ ] | E5 Delete material | Delete a test material. | Confirmation appears; row disappears after success; cancel leaves data unchanged. | **writes data**. |
| [ ] | E6 Snapshot list | Open material audit/snapshot list. | Snapshots load with timestamp/operator/action/count; pagination or refresh works. | |
| [ ] | E7 Rollback snapshot | Roll back to a known test snapshot. | Confirmation appears; rollback creates/uses expected snapshot; materials list reflects restored data. | **writes data**. Run only with test data. |
| [ ] | E8 CSV import | Import a valid small CSV and an invalid CSV. | Valid CSV imports and shows success/count; invalid CSV gives row/field error without corrupting current data. | **writes data**. |

## F. RAG Sync And Evaluation

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | F1 Sync/reload RAG | Trigger knowledge-base sync/reload. | Shows running state; success message includes synced count/collection info; failure shows readable backend/RAG error. | May update RAG service. |
| [ ] | F2 Evaluation status | Open RAG evaluation result/report panel. | Latest report loads; `quality_ok`, hit rate, MRR, and case count are displayed clearly. | |
| [ ] | F3 Evaluation trigger | Trigger a new evaluation when enabled. | UI shows running state; completion updates report id/status; failure keeps old report visible and shows error. | Can take time. |

## G. Quote Job Queue

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | G1 Job list | Open quote task queue in `admin.html`. | Jobs load with status/stage/user/time; `api_page` pagination renders correctly. | |
| [ ] | G2 Filter by status/user | Apply status and username filters. | Table updates to matching jobs; clearing filters restores full list. | |
| [ ] | G3 Job detail/events | Open a job detail or event view. | Stages/events are readable; result/error payloads do not break layout. | |
| [ ] | G4 Cancel job | Cancel a queued/running test job. | Confirmation appears; status becomes `cancelled`; running UI stops progressing. | **writes data**. |
| [ ] | G5 Retry failed job | Retry a failed/cancelled/timed-out test job. | New job is created; original job remains traceable; new job appears in list. | **writes data**. |
| [ ] | G6 Mark timeouts | Run timeout marking with safe threshold/test data. | Only eligible queued/running jobs are marked; success count is shown. | **writes data**. |

## H. Ops Monitoring And Alerts

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | H1 Ops dashboard load | Open the ops monitoring panel. | Overall status, service cards, stuck jobs, log clues, and alerts render without blank sections. | |
| [ ] | H2 Service status | Refresh service statuses. | MySQL, Redis, Celery, RAG, MinIO, and n8n show `ok/degraded/error` consistently with `/health/ready`. | |
| [ ] | H3 Log alert cleanup | With current fixed build, refresh ops logs. | Old Redis/Celery startup errors outside `OPS_LOG_LOOKBACK_MINUTES` are not counted as current abnormal clues. | Specifically guards the 167-log false positive. |
| [ ] | H4 Auto refresh | Wait at least one refresh interval. | Panel updates without duplicating alerts or resetting unrelated user input. | |

## I. Model Gateway Panel

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | I1 Stats load | Open model gateway stats. | Provider/model counts, success/failure metrics, latency, and estimated cost render clearly. | |
| [ ] | I2 Circuit state | Open circuit breaker state. | Circuit list loads; open/closed status and reset timing are readable; empty state is clear. | |
| [ ] | I3 Refresh | Click refresh. | Metrics update without losing layout or throwing parse errors. | |

## J. File Storage Panel

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | J1 Storage health | Open/check storage health. | Disabled MinIO is shown as disabled/acceptable; enabled MinIO shows health details or readable error. | |
| [ ] | J2 File list | Refresh file list. | List loads with filename, size, owner, created time, and purpose; empty state is readable. | |
| [ ] | J3 Upload file | Upload a small test file. | Upload progress/state is visible; success row appears after refresh; oversized/invalid file is rejected clearly. | **writes data**. |
| [ ] | J4 Download URL | Generate a download URL for an uploaded test file. | URL is returned; link opens or copies correctly; expired/error state is readable. | |

## K. Common API And Error Handling

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | K1 Unified response parsing | Exercise one endpoint returning `api_ok` and one returning `api_page`. | Frontend reads `data`/pagination consistently; no `undefined` table/list state appears. | Required after shared API changes. |
| [ ] | K2 401 response | Force a request with missing/invalid token. | User is redirected to `app.html`; a readable auth-expired message appears; no infinite request loop. | |
| [ ] | K3 403 response | Normal user hits admin-only endpoint. | UI shows permission error or redirects; admin panel data is not partially rendered. | |
| [ ] | K4 Network/backend error | Stop or mock one backend dependency, then refresh affected panel. | UI shows readable failure and retry/refresh option; page remains usable. | Use controlled test only. |
| [ ] | K5 Loading state | Trigger slow endpoints or long quote job. | Buttons/inputs show loading/disabled state where appropriate; duplicate submissions are prevented. | |

## L. Layout And Browser Smoke Checks

| Result | Scenario | Operation | Expected Result | Notes |
|---|---|---|---|---|
| [ ] | L1 Desktop layout | Check `app.html`, `index.html`, and `admin.html` at normal desktop width. | No overlapping text/buttons; primary workflows are visible; tables and dialogs fit the viewport. | |
| [ ] | L2 Narrow viewport | Resize to a narrow width or use mobile emulation. | Login and main workbench remain usable; admin tables may scroll horizontally but must not hide critical actions. | |
| [ ] | L3 Refresh persistence | Refresh each page while logged in. | Auth state persists; current page reloads cleanly; no duplicate SSE/task polling starts. | |
| [ ] | L4 Browser console smoke | Open devtools console while running one main flow. | No repeated uncaught exceptions; expected backend errors are handled by UI. | Manual check. |

---

## Change Exit Criteria

For each frontend change, record:

```text
Change:
Files touched:
Acceptance sections run:
Result:
Known skips:
Follow-up:
```

Minimum exit criteria:

- `app.html`, `index.html`, and `admin.html` still load.
- Login/auth checks A1 and A3 pass.
- The feature area touched by the change passes its section.
- No new blank page, infinite redirect, duplicate submission, or unreadable `undefined`/`[object Object]` output appears.
