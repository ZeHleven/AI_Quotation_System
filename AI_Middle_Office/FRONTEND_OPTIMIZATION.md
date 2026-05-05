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

- [ ] **P1 Shared browser logic**
  - Extract token handling, auth redirect, unified API response parsing, and common error handling into a shared static JS file.
  - Run acceptance sections A, K, and each touched page section after extraction.

- [ ] **P2 Admin module split**
  - Split admin-only feature areas such as ops, materials, quote jobs, model gateway, and files into isolated static JS modules without adding a build step.
  - Split one module at a time and run the corresponding acceptance section immediately.

- [ ] **P3 High-ROI UX improvements**
  - Improve quote progress, readable errors, retry entry points, upload/wait/push states, and long-task feedback.
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
