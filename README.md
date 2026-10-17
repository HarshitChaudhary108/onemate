# Outmate — AI GTM Research Sheets & Reports

Two surfaces over the same evidence rules — per-value status, per-claim
sources, and honest `unavailable` instead of invented answers:

- **Sheets** (`/`): paste a list of companies, describe the columns you want in
  plain language, and watch each cell fill in live. Every cell is one bounded
  Exa structured-output search; columns can reference earlier columns.
- **Research** (`/research`): the original single-company report — one
  deterministic pipeline over the **Exa** search API and optional **MoltSets**
  enrichment, now living at `/research` unchanged.

> **Verification status:** everything here is **fixture-tested / fake-tested**
> — the automated suites run on in-memory fake providers and synthetic payloads
> built from documented API shapes; no test performs a real provider call.
> **No live provider call was ever made for the sheets work** (owner amendment,
> 2026-10-17): U1–U4 in `docs/p0-openapi-notes.md` remain *unverified against
> the live API*, and **no cost figure is measured** — estimates are labelled
> estimates and spend displays come only from provider-returned `costDollars`.
> The only live call in project history remains one approved Exa identity
> search on 2026-10-16 (≈ $0.007 of free credit); **no live MoltSets or Groq
> call has ever been made** — see
> [What has not been live-tested](#what-has-not-been-live-tested).

## What it does

### Sheets workspace (`/`)

- Creates a sheet, **paste-imports** up to 100 companies per call (deduped by
  normalized domain, hard cap 200 rows).
- **Plans columns from a prompt**: a bounded Groq call proposes ≤ 3 columns
  (name, `{{…}}` template, output type, source scope, depth); the server
  re-validates everything the model claims — dependencies, unique names,
  cycles, scope policy — before a labelled estimate is shown.
- **Executes cells append-only**: each `(row, column)` cell is one Exa
  `/search` + `outputSchema` call, validated per output type and mapped to a
  `Field` status (`verified` only with a cited http(s) URL). New columns never
  rewrite existing cells.
- **Resolves row identity first** (reusing the report pipeline's identity
  logic); unresolved rows block all their cells with the reason, and no cell
  call is made for them.
- **Runs are capped and cancellable**: a per-run USD cap from
  provider-reported spend; 401/402 stops the run and returns queued cells to
  idle; a crash requeues `running` cells at next startup.
- **Streams live updates** over SSE; the grid merges per cell by revision
  (last write wins), reconnects and re-snapshots on drop, and never re-renders
  untouched cells.
- Persists everything in **SQLite** (`sheets`/`rows`/`columns`/`cells`/
  `cell_revisions`/`runs`; the cells table doubles as the job queue).

### Single-company research report (`/research`)

- Accepts **one company** (name and/or domain, plus an optional objective) and
  returns a versioned JSON report: per-field status, per-claim sources,
  missing fields, provider states, and structured errors.
- Resolves company identity through Exa first (with the bounded fallback);
  ambiguous or unsupported identities stop enrichment and say why.
- Runs focused Exa searches (identity, overview/market, growth/news,
  leadership) under a hard per-report budget, then optional MoltSets
  enrichment (≤ 5 people) only after a resolved identity.
- Builds a conservative GTM opportunity **hypothesis** — never generic sales
  copy, never fabricated intent — and survives partial provider failure.

## What it deliberately does not do

- No paid endpoints: no phone lookup, no personal-email lookup, no contact
  inference — this is enforced server-side by the planner's scope policy, not
  just promised in a prompt. No numeric buying-intent/lead scores.
- No CRM sync, outreach sequences, cold email, LinkedIn automation,
  ad-audience sync, CSV file upload, or list-building rows (paste import and
  research columns only).
- No accounts, billing, or multi-tenant code. The sheets API has **no
  authentication** — run it locally or behind a trusted boundary only.
- No autonomous multi-agent orchestration: the planner is one bounded Groq
  call whose output is fully re-validated server-side; cell execution is a
  deterministic pipeline, not an agent loop.
- No production deployment infrastructure. Single-process runtime only — the
  runner and event hub are in-process (no `--workers`).

*(Earlier versions of this README listed "no database, no LLM, no bulk" as
non-goals. The sheets work deliberately lifted them: SQLite persists sheets, a
Groq planner drafts columns under strict server-side validation, and paste
import + column runs are a bounded form of bulk.)*

## Architecture

```text
Browser
  │
  ▼
Next.js App Router UI (TypeScript / React)             frontend/
  │   /            sheet list + create
  │   /sheets/[id] workspace grid (virtualized rows, SSE-merged store)
  │   /research    single-company report (unchanged pipeline page)
  │
  ▼
FastAPI backend                                        backend/
  ├─ sheets HTTP API + SSE events                      app/api/sheets.py
  │    ├─ domain rules (append-only, limits, dedupe)   app/sheets/service.py
  │    ├─ SQLite store (cells table = job queue)       app/sheets/{db,models,repo}.py
  │    ├─ prompt→column planner (Groq, re-validated)   app/planner/* + app/policy/scope.py
  │    └─ engine                                       app/engine/*
  │         ├─ graph: {{Column}} deps, topo order, cycle rejection
  │         ├─ cell_exec: render → one Exa structured search → Field map
  │         ├─ runner: identity-first dispatch, blocked propagation,
  │         │          401/402 stop, cancel, crash requeue
  │         ├─ budget/limiter: per-run spend cap + token-bucket QPS
  │         └─ events: per-sheet hub → SSE (~50 ms batches)
  ├─ research pipeline (UNCHANGED)                     app/research/orchestrator.py
  │    ├─ Exa: identity search (1 call, always first)
  │    ├─ Exa: overview/market, growth/news, leadership (≤ 3 calls)
  │    └─ MoltSets: company + ≤ 5 people — after the identity gate
  ├─ providers                                         app/providers/
  │    ├─ exa.py: /search + optional outputSchema/systemPrompt/type
  │    ├─ groq.py: chat completions, strict JSON-Schema response_format
  │    └─ moltsets.py: company + people lookups
  ├─ normalization / status / report schema            app/normalize, app/policy, app/schemas
  └─ SQLite (SHEETS_DB_PATH, WAL, user_version guard)
```

Boundary rules:

- The browser never calls Exa, Groq, or MoltSets; FastAPI owns validation,
  provider calls, retries, and status semantics. `GROQ_API_KEY` missing →
  `503 groq_not_configured` at `/plan`; `EXA_API_KEY` missing → `503
  exa_not_configured` at `/runs` — never a silent mock.
- Columns are **append-only**; `depends_on` is always recomputed from the
  template, never taken from a caller or the planner.
- A cell is `verified` only when the structured output is `found`, the value
  validates for its type, and ≥ 1 grounded citation carries a usable http(s)
  URL at confidence `high`; medium/low confidence → `inferred`; everything
  else → `unavailable` with the reason in `note`.
- Provider text is untrusted data: upstream cell values are rendered into
  later prompts inside delimiters, length-capped, and marked data-not-
  instructions in the system prompt.
- SSE protocol: the client subscribes first, fetches the snapshot, then merges
  cell events by revision; a drop triggers reconnect + re-snapshot — no replay
  logic, no lost or duplicated cells.

Repository layout:

```text
backend/    FastAPI app (app/) + pytest suite (tests/)
frontend/   Next.js App Router UI (app/, components/, lib/) + Vitest suite (tests/)
docs/       spec.md, tech.md, implementation-plan.md, p0-openapi-notes.md
```

## Quickstart

Requirements: **Python 3.11+** (verified with 3.13.5) and **Node.js 20+**
(verified with Node 24.19.0 / npm 11.17.0).

```bash
git clone https://github.com/HarshitChaudhary108/onemate.git
cd onemate
```

### 1. Backend — create the environment and install

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1           # Windows PowerShell
# source .venv/Scripts/activate      # Windows Git Bash
# source .venv/bin/activate          # macOS / Linux

pip install -e ".[dev]"
```

### 2. Backend — create `.env`

```powershell
copy .env.example .env               # Windows
# cp .env.example .env               # macOS / Linux / Git Bash
```

For a **keyless sheets demo**, set in `backend/.env`:

```ini
EXA_MODE=mock
MOLTSETS_MODE=mock
GROQ_MODE=mock
```

> **Mock-mode caveat (pre-existing repo state, P0 commit `f5ec319`):** the
> original provider fixture files under `backend/tests/fixtures/` were never
> committed and are absent. `EXA_MODE=mock` therefore works **only** for
> structured-output cell searches (which synthesize results from the request
> schema); the identity resolution that precedes them, and the `/research`
> report scenarios, read the missing fixture files and will fail in this
> clone. A full sheet run needs a real `EXA_API_KEY` — the automated suite
> verifies everything else with in-memory fakes.

To use the real APIs, keep `EXA_MODE=live` / `GROQ_MODE=live` and add your
keys — see [Mock vs live mode](#mock-vs-live-mode).

### 3. Backend — start the server

```powershell
uvicorn app.main:app --port 8000
```

Leave it running. Verify once: <http://localhost:8000/health> →
`{"status":"ok"}`. The sheets database (`SHEETS_DB_PATH`, default `sheets.db`)
is created on startup, and any cell left `running` by a crash is requeued.

### 4. Frontend — install and create `.env`

Open a second terminal at the project root (keep the backend running):

```powershell
cd frontend
npm install
copy .env.example .env               # Windows
# cp .env.example .env               # macOS / Linux / Git Bash
```

`frontend/.env` sets `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` — how
the UI finds the backend (there is no built-in default).

### 5. Frontend — start the dev server

```powershell
npm run dev
```

Open <http://localhost:3000> → the sheets list. Create a sheet, **Import
rows**, describe a column in the prompt bar (Ctrl/⌘+K), review the plan card's
labelled estimate, and Add and run. The single-company report lives at
<http://localhost:3000/research>. See the mock-mode caveat above: without an
`EXA_API_KEY`, row identity cannot resolve in this clone — rows stay
`pending`, cells stay `queued`, and the run logs an internal error; the
workspace, planning, import, SSE, and persistence all still work.

### Optional: development checks

```powershell
# backend/
ruff format --check .
ruff check .
mypy app
pytest

# frontend/
npm run lint
npm run typecheck     # tsc --noEmit
npm test              # vitest run
npm run build
```

## Environment variables

Copy `backend/.env.example` → `backend/.env` and `frontend/.env.example` →
`frontend/.env`, then fill in what you need. Only variable names and safe
placeholders belong in the repo — **never commit real keys**.

| Variable | App | Default | Meaning |
|---|---|---|---|
| `EXA_API_KEY` | backend | *(blank)* | Exa API key. Blank in live mode means *not configured* (HTTP 503); it **never** enables mock mode. |
| `EXA_MODE` | backend | `live` | `live` or `mock`. `mock` uses synthetic results, always disclosed. |
| `MOLTSETS_API_KEY` | backend | *(blank)* | MoltSets API key. Blank in live mode means enrichment `not_configured` (no call, no fixtures). |
| `MOLTSETS_MODE` | backend | `live` | `live`, `mock`, or `off` (`off` reports the provider as disabled, no call). |
| `MOLTSETS_USER_AGENT` | backend | `outmate-prototype/1.0` | Non-empty User-Agent sent on every MoltSets request. |
| `GROQ_API_KEY` | backend | *(blank)* | Groq API key for the column planner. Blank in live mode → `503 groq_not_configured`, never a silent mock. |
| `GROQ_MODE` | backend | `live` | `live` or `mock` (deterministic synthetic plans). |
| `PLANNER_MODEL` | backend | `openai/gpt-oss-120b` | Groq model for planning calls; must support strict JSON Schema output. |
| `SHEETS_DB_PATH` | backend | `sheets.db` | SQLite file for sheets persistence (relative to the backend working dir). |
| `SHEET_MAX_ROWS` | backend | `200` | Hard row cap per sheet. |
| `SHEET_MAX_COLUMNS` | backend | `30` | Hard column cap per sheet. |
| `SHEET_MAX_CONCURRENCY` | backend | `4` | Concurrent cell executions per run. |
| `EXA_MAX_QPS` | backend | `5` | Token-bucket rate limit on Exa calls. |
| `RUN_COST_CAP_USD` | backend | `1.00` | Default per-run spend cap, enforced on provider-reported `costDollars.total`. |
| `CORS_ORIGINS` | backend | `http://localhost:3000` | Comma-separated explicit allowlist of frontend origins; `*` and empty values are rejected at startup. |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | *(blank)* | Public backend base URL — required so the UI can reach the backend. Not a secret; provider keys never appear in any `NEXT_PUBLIC_*` variable. |

Provider secrets exist only in the backend environment; they never appear in
logs, error bodies, the frontend bundle, or fixtures.

## Mock vs live mode

Mock mode is **explicit opt-in** and can never be triggered by a missing key
or a failed live call:

- **Live with key** → real provider calls. **Live without a key** → Exa fails
  with HTTP 503 `exa_not_configured`; Groq fails with 503
  `groq_not_configured`; MoltSets reports `not_configured`. Never fixtures.
- **Mock** → deterministic synthetic results, always disclosed: `mock: true`
  on sources and provider summaries, `data_mode: "mock"` (or `"mixed"`), a
  "Mock / sample data" banner on reports, and a provenance banner in the grid
  while any visible cell source is mock.
- **`MOLTSETS_MODE=off`** → provider disabled, no call.

Mock-mode coverage in this clone (see the caveat in Quickstart):

| Path | `mock` works here? |
|---|---|
| Sheet cell searches (`outputSchema` set) | Yes — synthesized from the request schema, no files needed |
| Groq planner (`GROQ_MODE=mock`) | Yes — deterministic synthetic plans |
| Row identity resolution, `/research` report scenarios, MoltSets | **No** — reads fixture files absent from this clone |

The report pipeline's mock scenarios (they need the missing fixtures):

| Input | Result |
|---|---|
| `example-robotics.test` | Full mock report; mock banner; `data_mode: "mock"` |
| `ambiguous` (e.g. `Example Ambiguous`) | Unresolved identity report; **no MoltSets calls**; note explains what was searched |
| `no-evidence.test` | Unresolved report: "no evidence for this domain" (identity + fallback = 2 Exa calls) |
| `fallback.test` | Full mock report via the bounded fallback (4 Exa calls; leadership skipped) |
| `moltsets-error.test` | Partial report: Exa evidence kept, MoltSets error listed, enrichment unavailable |
| anything else | Unresolved report; nothing is invented |

## Example request and response

**Illustrative, fixture-backed** — the report pipeline's mock output, trimmed
for readability (the referenced fixture is one of the files absent from this
clone).

```bash
curl -s http://localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"domain": "example-robotics.test"}'
```

```json
{
  "request": { "company_name": null, "domain": "example-robotics.test", "research_objective": null },
  "request_id": "req-fixture-0006",
  "schema_version": "1.0",
  "data_mode": "mock",
  "company_identity": {
    "resolved": true,
    "note": null,
    "company_name": {
      "status": "verified",
      "value": "Example Robotics Ltd",
      "note": null,
      "sources": [
        {
          "provider": "Exa",
          "mock": true,
          "title": "Example Robotics Ltd — Warehouse Automation",
          "url": "https://example-robotics.test/",
          "highlight": "Example Robotics Ltd builds autonomous robots for warehouse picking.",
          "published_date": "2025-11-02",
          "retrieved_at": "2026-10-16T12:00:00Z",
          "query": "example-robotics.test company"
        }
      ]
    }
  },
  "overall_status": "provider-reported",
  "errors": [],
  "providers": {
    "exa": { "state": "ok", "mock": true, "calls": 4 },
    "moltsets": { "state": "mock", "mock": true, "calls": 2 }
  }
}
```

Reading guide:

- `data_mode` — `live`, `mock`, or `mixed`, derived from the data that
  actually contributed.
- Every factual field is a wrapper: `value` + `status` + `sources` (+ optional
  `note`). A `verified` value always carries ≥ 1 source with an HTTP(S) URL;
  MoltSets sources legitimately have `url: null`, so their values stay
  `provider-reported`. Sheet cells reuse this exact `Field` shape.
- `providers` — per-provider state, `mock` flag, **actual call count**
  (including retries), and usage metadata only when the provider returned it.
- `errors` / `missing_fields` — structured, display-safe errors (with
  `retryable` and `request_id`), and factual fields left `unavailable`.

## Status semantics

Report `Field` statuses (reused verbatim for cell results):

| Status | Meaning |
|---|---|
| `verified` | Supported by cited public-source evidence; the evidence must actually support the displayed value, and at least one source must carry an HTTP(S) URL. |
| `provider-reported` | Returned by a structured provider (e.g. MoltSets) without independent cited evidence for that value. |
| `inferred` | A reasoned conclusion from cited evidence — for cells, a grounded value whose best citation confidence is medium or low. |
| `unavailable` | Missing, unsupported, or unsafe to resolve confidently — always with the reason in `note`. |

`mock` is **not** a status — it is separate provenance metadata (`mock: true`
plus the top-level `data_mode`). `providers.*.state` is separate too (`ok`,
`partial`, `error`, `not_configured`, `mock`, `disabled`, `not_found`,
`quota_exhausted`).

`overall_status` is computed from the seven factual fields only (the GTM
hypothesis is excluded): `unavailable` when the identity is unresolved or no
factual field has a value; otherwise `inferred` if any valued field is
inferred, else `provider-reported` if any is provider-reported, else
`verified`. Missing optional fields do not downgrade an otherwise useful
report.

### Cell execution states (sheets)

Separate from the `Field` status above — a cell's *lifecycle* state:

`idle → queued → running → done | error`. A queued cell becomes `blocked`
when its upstream failed or is unavailable, or when its row's identity never
resolved (the error text names the reason). Cancel returns queued cells to
`idle`; a crash requeues `running` cells at startup. A prompt edit bumps the
column version and marks dependent cells `stale` — it never wipes values.

## Sheets API

All under `/api`, on the same `ErrorResponse` shape as the report pipeline
(`not_found` → 404; name/reference/run conflicts → 409; scope and validation
violations → 422; missing provider keys → 503):

- `POST /api/sheets` · `GET /api/sheets` · `GET /api/sheets/{id}/snapshot` ·
  `DELETE /api/sheets/{id}` (cascades rows, columns, cells, revisions, runs)
- `POST /api/sheets/{id}/rows` — paste import, ≤ 100 rows per call, deduped by
  normalized domain
- `POST /api/sheets/{id}/plan` — stateless planner preview (needs Groq)
- `POST /api/sheets/{id}/columns` — atomic create with optional immediate run;
  `PATCH`/`DELETE /api/sheets/{id}/columns/{col}` (rename → 409 when
  referenced, delete → 409 while dependents exist)
- `POST /api/sheets/{id}/runs` — scope `empty|stale|error|all` (`all` needs
  `confirm_overwrite=true`, one active run per sheet); `POST
  /api/sheets/{id}/runs/{run_id}/cancel`
- `GET /api/sheets/{id}/cells/{row}/{col}` — value, `Field` status, sources,
  rendered prompt, cost, timestamps, revision history
- `GET /api/sheets/{id}/export.csv` — CSV; values starting `= + - @` are
  prefixed (formula-injection guard)
- `GET /api/sheets/{id}/events` — SSE cell/row/run events batched ~50 ms

Hard limits: 200 rows, 30 columns, 100 rows per import call. **No
authentication** — local or trusted-boundary deployment only.

## Identity resolution

Shared by the report pipeline and sheet rows (the same `resolve_identity`):

- **Domain supplied:** resolved iff ≥ 1 Exa hit's host matches the normalized
  domain (subdomains count, `www.` ignored). If a name is supplied too, a
  domain-matching hit must also name-match — otherwise ambiguous ("supplied
  name and domain do not agree"). No domain-matching hit → unresolved ("no
  evidence for this domain").
- **Bounded identity fallback:** when the identity search finds no pages on
  the requested domain, exactly one broader fallback search runs — same
  subject and domain scoping (`includeDomains`), with the `company` category
  omitted. If it resolves, the leadership search is skipped in the report
  pipeline.
- **Name only:** the top 5 name-matching hits must all share a single host;
  multiple hosts → ambiguous, no matching hits → unresolved.
- A resolved identity's name/domain are `verified` with the supporting hit(s)
  as sources — the user's input alone is never a citation. Unresolved or
  ambiguous rows in a sheet block every cell with the reason; unresolved
  reports return immediately with MoltSets never called.

## Provider notes

**Exa** (`backend/app/providers/exa.py`)

- `POST https://api.exa.ai/search`, authenticated with the `x-api-key` header;
  key read only from `EXA_API_KEY` in the backend process.
- Report-pipeline request shape is unchanged: `type: "auto"`,
  `numResults ≤ 5`, `contents: {highlights: true}`; optional `includeDomains`;
  date filters only with `category: "news"`. Requests that set none of the new
  fields produce byte-identical bodies to before the sheets work (tested).
- Cell searches add `outputSchema` (`{found, value}` — only `type`,
  `description`, `properties` keys; spec limits respected), `systemPrompt`,
  and `type` from the column depth (`auto`/`deep`; `deep-reasoning` is not a
  legal value). The structured `output` is parsed tolerantly: missing or
  malformed → treated as no output, never an error.
- 30 s timeout / 5 s connect; empty `results` is a normal no-evidence outcome;
  `costDollars` is preserved verbatim (an object — spend reads
  `costDollars.total`).
- Errors: 4xx not retryable; 429 / timeout / network / 5xx retry once; a
  missing key in live mode → `exa_not_configured`, no call is made.
- People scope never sends `includeDomains` or date filters; company-site
  scope sends `includeDomains` with no category; news scope sends
  `category: "news"` with a 365-day window (per the documented Exa 400s).

**Groq** (`backend/app/providers/groq.py`)

- `POST https://api.groq.com/openai/v1/chat/completions`,
  `Authorization: Bearer`; used only by the column planner.
- Strict structured output: `response_format: {type: "json_schema",
  json_schema: {…, strict: true}}` — every object lists all properties in
  `required` and sets `additionalProperties: false`. Never `tools` /
  `tool_choice` in the planner request (Groq documents they can't combine
  with Structured Outputs).
- `choices[0].message.content` is parsed as JSON and re-validated by Pydantic
  plus the domain rules (the model's claims are never trusted). One repair
  retry on invalid output, then a structured failure.
- One retry on 429 / 5xx / timeouts, mirroring the Exa boundary. Errors map to
  a separate `LlmError` (provider `"Groq"`) — never into the frozen
  `Literal["Exa","MoltSets"]` contract.
- Planner intents: `add_columns` (≤ 3), `needs_clarification` (one question),
  `unsupported` (with reason — the scope policy refuses phone numbers,
  personal emails, and contact inference).

**MoltSets** (`backend/app/providers/moltsets.py`)

- Base `https://api.moltsets.com/api/v1/tools/`; only `search_companies` and
  `search_people` are ever called — phone, personal-email, and every other
  tool are out of scope.
- `Authorization: Bearer` + a non-empty `User-Agent` on every request.
- Company lookup by the confirmed domain is cached in process memory (errors
  never cached); people searches are never cached and hard-capped at 5.
- `business_email_risk_score` is a deliverability signal, never identity
  proof — the fixed note "deliverability signal, not identity proof" travels
  with every contact.

**Reliability**

- At most one retry per request, only for transient failures (timeout,
  network error, 5xx, or a rate limit with a usable retry time), honored
  within a 10 s budget — a longer required wait means no retry; retries
  without a hint use a fixed 1.0 s bounded backoff.
- One provider failing never erases the other's evidence: failures are
  recorded per stage and every structured error is listed.
- A cell run stops entirely on Exa 401/402 (invalid key / credits exhausted)
  and returns its queued cells to idle — never N identical errors.

**HTTP response mapping**

| Situation | HTTP |
|---|---|
| Report built (including partial / unresolved / no-evidence) | 200 |
| Sheet created / rows imported / columns created | 201 |
| Run accepted for dispatch | 202 |
| Sheet or column deleted | 204 |
| Sheet resource missing | 404 |
| Column rename/delete blocked by dependents, run already active, `all` without confirm | 409 |
| Request validation or sheet domain-rule failure | 422 (safe error body, no input echo) |
| Exa / Groq not configured | 503 `exa_not_configured` / `groq_not_configured` |
| Provider failure with no usable evidence | 502 |
| Unexpected internal failure | 500 (generic message + `request_id`; traceback logged server-side only) |

Every response carries `X-Request-ID` and `Cache-Control: no-store`.

## Cost discipline and free-tier limits

- **Never incur unapproved paid usage:** no payment method, no plan upgrades,
  no paid endpoints. Automated tests never call providers — all HTTP is
  faked at the transport layer and all providers are in-memory fakes.
- **Per-run cap:** each sheet run carries a USD cap (`RUN_COST_CAP_USD`,
  default $1.00; per-call override). Spend is counted only from
  provider-returned `costDollars.total`; the run stops before a cell whose
  labelled estimate would exceed the cap, and returns remaining cells to
  idle. Estimates use the documented upper-bound `/search` price so an
  estimate is never below actual spend in mock runs (tested).
- **Preview before spend:** the plan card shows a labelled estimate computed
  server-side; nothing runs until the user confirms.
- MoltSets free tier: **100 lifetime search records**, **max 5 records per
  call** — the adapter caps people searches at 5 in code.
- Per-report budget (research pipeline, unchanged): **≤ 4 Exa searches**
  (identity, overview/market, growth/news, leadership — objective folded into
  overview) + ≤ 1 permitted retry each; **≤ 1 MoltSets company + ≤ 1 people
  call** + ≤ 1 retry each. No exploratory re-queries except the single
  bounded identity fallback; post-identity Exa concurrency ≤ 2.
- Usage/quota figures are displayed only when the provider actually returns
  them — never invented. The toolbar's "Spent" sums only provider-reported
  per-cell costs; estimates are always labelled.
- Live smoke tests are manual and opt-in only: one approved Exa identity
  search ran on 2026-10-16 (≈ $0.007 of free credit). **No live call was made
  for the sheets work** — no measured cost exists for cell runs.

## Known limitations and open gates

- **Public evidence may be incomplete.** `unavailable` means "not supported in
  this run", not "the company has none".
- **No live verification of the sheets path.** Exa `outputSchema` /
  `systemPrompt` / `type: "deep"` behavior, `grounding[].field` strings, and
  Groq model access + strict-schema shape are implemented from documented
  specs only (`docs/p0-openapi-notes.md`); the first live run may surface
  deviations — unmatched grounding becomes `unavailable`, never guessed.
- **Missing fixture suite (pre-existing, P0 `f5ec319`).** The original
  backend pytest suite and all `backend/tests/fixtures/**` inputs were never
  committed; restoring them is an owner decision — they were not recreated
  from imagination. Consequences: `pytest` collects only the sheets-era suite;
  19 pre-existing frontend tests fail `ENOENT` on those fixtures;
  `EXA_MODE=mock` works only for structured cell searches (see Mock vs live).
- **The sheets API has no authentication.** Local / trusted-boundary only.
- **Single process.** The runner and event hub are in-process; multi-worker
  deployment would break the in-memory queue (D3, documented).
- **A crash can re-charge a running cell** — accepted and documented (plan
  risk #5); `cell_revisions` keeps every completed write.
- **MoltSets signup/access is unconfirmed**, and the people/company search
  tools may be alpha. Endpoint paths and schemas are isolated in
  `backend/app/providers/moltsets.py` and must be re-checked against current
  docs before any live call (see `docs/provider-notes.md`).
- **The GTM opportunity is a hypothesis, not purchase intent** — the UI labels
  it "Inferred hypothesis".
- **Provider text is untrusted data.** Length-capped, rendered as text only;
  a hostile page could still contain misleading claims — evidence is quoted,
  not endorsed. Upstream cell values are delimited and capped inside later
  prompts.
- **Unverified assumptions** (full list in `docs/provider-notes.md`):
  retry-hint formats and the 10 s retry budget are implementation choices;
  `costDollars` is taken verbatim; `exa_invalid_response` /
  `moltsets_invalid_response` are internal codes for unparseable 200 bodies.
- **SSE through the request middleware was a named unknown (U6)** — resolved
  by integration test: events flow correctly (the middleware remains
  `BaseHTTPMiddleware`).

## What has not been live-tested

Recorded honestly per the project's verification vocabulary:

- **Live-tested (ever):** one approved Exa identity search on 2026-10-16,
  ≈ $0.007 — the report pipeline only.
- **Never live-tested:** every sheets path (planning, cell execution, SSE,
  persistence under real load), Exa `outputSchema`/`systemPrompt`/`deep`,
  `grounding[].field` values for our schema, Groq model access and
  strict-schema responses, MoltSets (any endpoint, ever), real cost of any
  cell or run (U1 remains open — no measured cost exists anywhere).
- **Locally run:** the app has been exercised end-to-end only where mock or
  fake providers could substitute; a full sheet run against `EXA_MODE=mock`
  is limited by the missing fixtures (above).

## Demo checklist

1. Start the backend (above); confirm `GET http://localhost:8000/health`
   returns `{"status":"ok"}`.
2. Start the frontend (`npm run dev`) and open <http://localhost:3000>.
3. Sheets: create a sheet → Import rows (paste `Acme Corp,acme.com` and
   `Globex,globex.io`) → Ctrl/⌘+K → "Industry for each company" → review the
   plan card's labelled estimate → **Add and run**. With `EXA_MODE=mock` in
   this clone, identity resolution cannot complete (missing fixtures) — rows
   stay `pending`, cells stay `queued`, the run logs an internal error;
   planning, import, the grid, SSE, and persistence all work.
4. `/research`: the mock scenarios under Mock vs live mode need the absent
   fixture files — they fail in this clone (pre-existing state, not a
   regression).
5. Optional live run — only with an approved key and free credits: set
   `EXA_API_KEY` (and optionally `GROQ_API_KEY` / `MOLTSETS_API_KEY`), remove
   the mock env vars, restart the backend, and run **one** sheet cell.
