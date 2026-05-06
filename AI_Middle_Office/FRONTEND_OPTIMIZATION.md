# Frontend Optimization Track

> Status: start after backend infrastructure freeze. Do not migrate to Vite/Vue SFC yet; first create acceptance coverage, reduce duplicated browser logic, and split the oversized admin page safely.

## Principles

- Keep the current FastAPI-hosted HTML deployment model.
- Do not introduce a frontend build step until there is a real trigger.
- Every frontend refactor must run the affected section of `FRONTEND_ACCEPTANCE.md`.
- Prefer user-visible improvements over framework migration.
- Treat acceptance as the exit condition for each step.

## Roadmap

- [x] **P0 Frontend acceptance checklist**
  - Added `AI_Middle_Office/FRONTEND_ACCEPTANCE.md`.
  - Checklist entries use scenario, operation, expected result, and notes/risk.
  - Covers login/auth, quote workflow, history, admin permissions, materials, RAG sync/evaluation, quote jobs, ops monitoring, model gateway, file storage, common errors, and layout smoke checks.

- [x] **P1 Shared browser logic**
  - Added `static/js/shared.js` with token/user storage helpers, auth header creation, unified API response parsing, common error extraction, and axios 401 handling.
  - `index.html` and `admin.html` now load the shared helper instead of carrying duplicate token/API parsing code.
  - Acceptance focus for future verification: A1, A3, B1, C1, D2, H1, and K1-K3.

- [x] **P2 Admin module split**
  - [x] `ops`: moved ops dashboard state, alert notification, refresh, and polling into `static/js/admin/ops.js`.
  - [x] `materials`: moved knowledge-base CRUD, snapshots, rollback, CSV import, Milvus sync, and RAG eval polling into `static/js/admin/materials.js`.
  - [x] `quote_jobs`: moved job queue listing, filters, detail, cancel, retry, timeout marking, status tags, and detail formatting into `static/js/admin/quote_jobs.js`.
  - [x] `model_gateway`: moved stats/circuit state and refresh logic into `static/js/admin/model_gateway.js`.
  - [x] `files`: moved storage health, file list, upload, and download URL logic into `static/js/admin/files.js`.
  - Split one module at a time and run the corresponding acceptance section immediately.

- [x] **P3 High-ROI UX improvements**
  - [x] `quote_progress`: expanded the workbench quote flow to five visible stages, added current status text, elapsed seconds, long-running generation hint, empty-request validation, and clearer error progress state.
  - [x] `retry_entry`: failed quote bubbles now expose task id/trace id, per-message retry, and task-status recovery that can reopen pre-review results.
  - [x] `upload_push_states`: upload controls now show file metadata and lock during quote runs; confirm push locks editing/cancel/duplicate submit and keeps retryable errors in the dialog.
  - These may be pulled forward when they are safer or more valuable than structural cleanup.

- [ ] **P4 Vite/Vue migration watch**
  - Revisit Vite + Vue SFC only if page count, team size, component reuse, TypeScript, router, or state-management needs justify the migration cost.

## Migration Triggers

Consider a Vite/Vue migration only when at least one of these becomes true:

- More frontend pages are added and manual HTML coordination becomes slow.
- Multiple developers need to work on frontend modules in parallel.
- Component reuse becomes painful with CDN Vue and static HTML.
- TypeScript, router, or centralized state management becomes a real requirement.
- The cost of maintaining static HTML exceeds the cost of adding build/deploy complexity.
