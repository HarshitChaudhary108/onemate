# Outmate frontend

Next.js (App Router, TypeScript) UI for Outmate — the sheets workspace and the
single-company research report. It talks only to the FastAPI backend in
`../backend` via `NEXT_PUBLIC_API_BASE_URL`; it never calls Exa, Groq, or
MoltSets directly, and provider keys never appear in `NEXT_PUBLIC_*`
variables.

## Routes

- `/` — sheets list: create and open sheets.
- `/sheets/[sheetId]` — the workspace: virtualized grid, docked prompt bar
  (Ctrl/⌘+K), cell inspector, paste-import dialog, SSE-driven live cell
  updates merged by revision.
- `/research` — the original single-company report page, moved here unchanged
  when `/` became the sheets entry.

## Setup

```bash
npm install
cp .env.example .env          # Windows: copy .env.example .env
# .env sets NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev                   # http://localhost:3000 — needs the backend for API calls
```

## Checks

```bash
npm run lint
npm run typecheck             # tsc --noEmit
npm test                      # vitest run
npm run build
```

## Verification status

- Component/store/client tests run entirely on mocks — a fake `EventSource`,
  module-mocked API client, and store-level event injection; no test performs
  a real network call.
- **No live provider call has ever been made** through this app; nothing here
  has been verified against a live backend beyond the automated suites.
- **19 pre-existing test failures** are `ENOENT` on `../backend/tests/
  fixtures/{report.schema.json,reports/*.json}` — fixture files that were
  never committed (pre-existing repo state, documented in the root README);
  every test added for the sheets work passes independently.
- Lint reports one known warning: `react-hooks/incompatible-library` on
  `useVirtualizer` in the grid — accurate notice; render isolation is covered
  by a dedicated test instead of relying on library memoization.
