# Outmate — AI GTM Research & Enrichment Prototype

Enter a company name or website, optionally state a research objective, and get a
readable, evidence-backed GTM research report. A Next.js UI talks to a Python/FastAPI
backend that runs one deterministic research pipeline over the **Exa** search API and
optional **MoltSets** enrichment.

> **Verification status:** everything here is **fixture-tested**, and the mock path is
> **locally tested**. One approved live Exa smoke search was made on 2026-10-16
> (identity query only, ≈ $0.007 of free credit); **no live MoltSets call has ever been
> made** — see [What has not been live-tested](#what-has-not-been-live-tested).

## What it does

- Accepts **one company** (name and/or domain, plus an optional objective) and returns
  a versioned JSON report: per-field status, per-claim sources, missing fields,
  provider states, and structured errors.
- Resolves company identity through Exa first; ambiguous or unsupported identities
  stop enrichment and say why.
- Runs focused Exa searches (identity, overview/market, growth/news, leadership) under
  a hard per-report budget, then optional MoltSets company + people enrichment
  (≤ 5 people, C-suite / VP / Owner) only after a resolved identity.
- Builds a conservative GTM opportunity **hypothesis** from the objective plus cited
  signals — never generic sales copy, never fabricated intent.
- Survives partial failure: one provider failing keeps the other provider's evidence
  and lists the error.
- Offers an explicit, visible **mock mode** with deterministic synthetic fixtures and
  honest `mock` provenance everywhere.

## What it deliberately does not do

- No bulk lists, CSV import, CRM sync, outreach sequences, cold email, LinkedIn
  automation, or ad-audience sync.
- No database, accounts, billing, queues, background jobs, or multi-tenant code — the
  only cache is a process-local MoltSets company lookup keyed by domain.
- No separate LLM provider; descriptions and the hypothesis are deterministic
  templates over captured evidence.
- No paid endpoints: no phone lookup, no personal-email lookup, no contact inference,
  no numeric buying-intent/lead scores.
- No autonomous multi-agent orchestration — the workflow is a deterministic pipeline.
- No production deployment infrastructure.

## Architecture

```text
Browser
  │
  ▼
Next.js App Router UI (TypeScript / React)          frontend/
  │   HTTP POST  NEXT_PUBLIC_API_BASE_URL + /api/research
  ▼
FastAPI backend                                     backend/
  ├─ Pydantic request validation                    app/schemas/research.py
  ├─ X-Request-ID + Cache-Control + error boundary  app/main.py
  ├─ input / domain normalization                   app/normalize/domain.py
  ├─ research orchestrator                          app/research/orchestrator.py
  │    ├─ Exa: identity search (1 call, always first)
  │    ├─ Exa: overview/market, growth/news, leadership (3 calls, concurrency ≤ 2)
  │    ├─ MoltSets: company lookup — only after the identity gate
  │    └─ MoltSets: ≤ 5 people — only after a company match
  ├─ normalization / precedence / sources           app/normalize/report.py
  ├─ field status + missing fields                  app/policy/status.py
  └─ validated versioned JSON report                app/schemas/report.py
  │
  ▼
Next.js renders the report, provider states, and mock banner
```

Boundary rules:

- The browser never calls Exa or MoltSets; there is no research endpoint in Next.js.
  FastAPI owns validation, provider calls, retries, and status semantics.
- Every source is attached to the exact field it supports; a source elsewhere in the
  report never makes a field `verified`.
- Provider responses are treated as untrusted data (length-capped strings,
  http(s)-only URLs, no HTML injection, no instruction following).

Repository layout:

```text
backend/    FastAPI app (app/) + pytest suite (tests/)
frontend/   Next.js App Router UI (app/, components/, lib/) + Vitest suite (tests/)
docs/       spec.md, tech.md, implementation-plan.md
```

## Quickstart

No API keys are needed: the backend runs in **mock mode** against synthetic fixtures,
and the frontend works once both servers are up.

Requirements: **Python 3.11+** (verified with 3.13.5) and **Node.js 20+** (verified
with Node 24.19.0 / npm 11.17.0).

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

### 2. Backend — create `.env` and enable mock mode

```powershell
copy .env.example .env               # Windows
# cp .env.example .env               # macOS / Linux / Git Bash
```

Open `backend/.env` and set:

```ini
EXA_MODE=mock
MOLTSETS_MODE=mock
```

That is the whole configuration for a keyless demo. Mock mode is explicit and always
disclosed (`mock: true` on sources, `data_mode: "mock"`, "Mock / sample data" banner).
To use the real APIs instead, keep `EXA_MODE=live` and add your keys — see
[Mock vs live mode](#mock-vs-live-mode).

### 3. Backend — start the server

```powershell
uvicorn app.main:app --port 8000
```

Leave it running. Verify once: <http://localhost:8000/health> → `{"status":"ok"}`.

### 4. Frontend — install and create `.env`

Open a second terminal at the project root (keep the backend running):

```powershell
cd frontend
npm install
copy .env.example .env               # Windows
# cp .env.example .env               # macOS / Linux / Git Bash
```

`frontend/.env` sets `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` — how the UI
finds the backend (there is no built-in default). Change it only if your backend runs
elsewhere.

### 5. Frontend — start the dev server

```powershell
npm run dev
```

Open <http://localhost:3000>, submit `example-robotics.test`, and you should see the
full mock report with the "Mock / sample data" banner. More built-in scenarios are
listed under [Mock vs live mode](#mock-vs-live-mode). If the UI reports "Could not
reach the research service", check that the backend is still running and
`frontend/.env` points at it.

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
`frontend/.env`, then fill in what you need. Only variable names and safe placeholders
belong in the repo — **never commit real keys**.

| Variable | App | Default | Meaning |
|---|---|---|---|
| `EXA_API_KEY` | backend | *(blank)* | Exa API key. Blank in live mode means *not configured* (HTTP 503); it **never** enables mock mode. |
| `EXA_MODE` | backend | `live` | `live` or `mock`. `mock` uses deterministic fixtures and is always disclosed. |
| `MOLTSETS_API_KEY` | backend | *(blank)* | MoltSets API key. Blank in live mode means enrichment `not_configured` (no call, no fixtures). |
| `MOLTSETS_MODE` | backend | `live` | `live`, `mock`, or `off` (`off` reports the provider as disabled, no call). |
| `MOLTSETS_USER_AGENT` | backend | `outmate-prototype/1.0` | Non-empty User-Agent sent on every MoltSets request (the API rejects missing/blank UAs). |
| `CORS_ORIGINS` | backend | `http://localhost:3000` | Comma-separated explicit allowlist of frontend origins; `*` and empty values are rejected at startup. |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | *(blank)* | Public backend base URL — required so the UI can reach the backend (set it in `frontend/.env`, see Quickstart). Not a secret; provider keys never appear in any `NEXT_PUBLIC_*` variable. |

Provider secrets exist only in the backend environment; they never appear in logs,
error bodies, the frontend bundle, or fixtures (enforced by the security audit tests).

## Mock vs live mode

Mock mode is **explicit opt-in** and can never be triggered by a missing key or a
failed live call:

- **Live with key** → real provider calls. **Live without a key** → Exa fails with
  HTTP 503 `exa_not_configured`; MoltSets reports `not_configured`. Never fixtures.
- **Mock** → deterministic fixtures, always disclosed three ways: `mock: true` on
  sources and provider summaries, `data_mode: "mock"` (or `"mixed"`), and a persistent
  "Mock / sample data" banner in the UI. Fixture people are named "Sample … (fixture)"
  with emails only on reserved `.test`/`example.com` domains.
- **`MOLTSETS_MODE=off`** → provider disabled, no call.

With mock mode on, the input selects a deterministic scenario (case-insensitive
substring):

| Input | Result |
|---|---|
| `example-robotics.test` | Full mock report; mock banner; `data_mode: "mock"` |
| `ambiguous` (e.g. `Example Ambiguous`) | Unresolved identity report; **no MoltSets calls**; note explains what was searched |
| `no-evidence.test` | Unresolved report: "no evidence for this domain" (identity + fallback = 2 Exa calls) |
| `fallback.test` | Full mock report via the bounded fallback: the primary identity search finds nothing; the broader fallback resolves the domain (4 Exa calls; leadership skipped) |
| `moltsets-error.test` | Partial report: Exa evidence kept, MoltSets error listed, enrichment unavailable |
| anything else | Unresolved report; nothing is invented |

## Example request and response

**Illustrative, fixture-backed** — produced by the real pipeline running in mock mode,
trimmed for readability (full snapshot: `backend/tests/fixtures/reports/mock.json`).

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
    },
    "domain": {
      "status": "verified",
      "value": "example-robotics.test",
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
  "warnings": [],
  "providers": {
    "exa": { "state": "ok", "mock": true, "calls": 4 },
    "moltsets": { "state": "mock", "mock": true, "calls": 2 }
  }
}
```

Reading guide:

- `data_mode` — `live`, `mock`, or `mixed`, derived from the data that actually
  contributed.
- Every factual field is a wrapper: `value` + `status` + `sources` (+ optional
  `note`). A `verified` value always carries ≥ 1 source with an HTTP(S) URL; MoltSets
  sources legitimately have `url: null`, so their values stay `provider-reported`.
  The remaining fields (`description`, `employee_size`, `growth_signals`,
  `target_market`, `contact_information`, `decision_makers`,
  `potential_gtm_opportunity`) follow the same wrapper shape.
- `providers` — per-provider state, `mock` flag, **actual call count** (including
  retries), and usage metadata only when the provider returned it.
- `errors` / `missing_fields` — structured, display-safe errors (with `retryable` and
  `request_id`), and factual fields left `unavailable` in this run.

## Status semantics

| Status | Meaning |
|---|---|
| `verified` | Supported by cited public-source evidence; the evidence must actually support the displayed value, and at least one source must carry an HTTP(S) URL. |
| `provider-reported` | Returned by a structured provider (e.g. MoltSets) without independent cited evidence for that value. |
| `inferred` | A reasoned conclusion from cited evidence and the objective — used by the GTM hypothesis. |
| `unavailable` | Missing, unsupported, or unsafe to resolve confidently. |

`mock` is **not** a status — it is separate provenance metadata (`mock: true` plus the
top-level `data_mode`). `providers.*.state` is separate too (`ok`, `partial`, `error`,
`not_configured`, `mock`, `disabled`, `not_found`, `quota_exhausted`).

`overall_status` is computed from the seven factual fields only (the GTM hypothesis is
excluded): `unavailable` when the identity is unresolved or no factual field has a
value; otherwise `inferred` if any valued field is inferred, else `provider-reported`
if any is provider-reported, else `verified`. Missing optional fields do not downgrade
an otherwise useful report.

## Identity resolution

- **Domain supplied:** resolved iff ≥ 1 Exa hit's host matches the normalized domain
  (subdomains count, `www.` ignored). If a name is supplied too, a domain-matching hit
  must also name-match — otherwise ambiguous ("supplied name and domain do not
  agree"). No domain-matching hit → unresolved ("no evidence for this domain").
- **Bounded identity fallback:** when the identity search finds no pages on the
  requested domain, exactly one broader fallback search runs — same subject and domain
  scoping (`includeDomains`), with the `company` category omitted so Exa searches all
  indexed pages on the domain instead of the company index alone. If the fallback
  resolves, the leadership search is skipped, so the report still makes at most four
  Exa searches. If it does not, the run stays unresolved with the same honest note,
  and MoltSets is never called.
- **Name only:** the top 5 name-matching hits must all share a single host; multiple
  hosts → ambiguous, no matching hits → unresolved.
- A resolved identity's name/domain are `verified` with the supporting hit(s) as
  sources — the user's input alone is never a citation. Unresolved or ambiguous runs
  return immediately: identity-dependent fields stay `unavailable` and **MoltSets is
  never called**.

## Provider notes

**Exa** (`backend/app/providers/exa.py`)

- `POST https://api.exa.ai/search`, authenticated with the `x-api-key` header; key
  read only from `EXA_API_KEY` in the backend process.
- Request shape: `type: "auto"`, `numResults ≤ 5`, `contents: {highlights: true}`;
  optional `includeDomains` for the identity search; publication-date filters only
  with `category: "news"` (anything else is rejected locally before any call).
- 30 s timeout / 5 s connect; empty `results` is a normal no-evidence outcome, not an
  error; `costDollars` is preserved verbatim when present.
- Errors: 4xx are not retryable; 429 / timeout / network / 5xx retry once; a missing
  key in live mode → `exa_not_configured`, no call is made.

**MoltSets** (`backend/app/providers/moltsets.py`)

- Base `https://api.moltsets.com/api/v1/tools/`; only `search_companies` and
  `search_people` are ever called — phone, personal-email, and every other tool are
  out of scope.
- `Authorization: Bearer` + a non-empty `User-Agent` on every request (a blank UA
  fails locally before any call).
- Company lookup by the confirmed domain is cached in process memory (errors never
  cached); people searches are never cached and are hard-capped at 5 in code.
- HTTP 200 + `status: "not_found"` is a normal miss. `business_email_risk_score` is a
  deliverability signal, never identity proof — the fixed note "deliverability signal,
  not identity proof" travels with every contact.

**Reliability**

- At most one retry per request, only for transient failures (timeout, network error,
  5xx, or a rate limit with a usable retry time), honored within a 10 s budget — a
  longer required wait means no retry, so the UI is never left hanging; retries
  without a hint use a fixed 1.0 s bounded backoff.
- One provider failing never erases the other's evidence: failures are recorded per
  stage (Exa per query, MoltSets per enrichment field) and every structured error is
  listed in the report.

**HTTP response mapping**

| Situation | HTTP |
|---|---|
| Report built (including partial / unresolved / no-evidence) | 200 |
| Request validation failed | 422 (safe error body, no input echo) |
| Exa not configured | 503 `exa_not_configured` |
| Identity search failed with no usable evidence | 502 |
| Unexpected internal failure | 500 (generic message + `request_id`; traceback logged server-side only) |

Every response carries `X-Request-ID` and `Cache-Control: no-store`.

## Cost discipline and free-tier limits

- **Never incur unapproved paid usage:** no payment method, no plan upgrades, no paid
  endpoints. Automated tests never call providers (a network guard blocks
  non-loopback sockets; all HTTP is mocked).
- MoltSets free tier: **100 lifetime search records**, **max 5 records per call** —
  the adapter caps people searches at 5 in code.
- Per-report budget: **≤ 4 Exa searches** (identity, overview/market, growth/news,
  leadership — the objective is folded into the overview query) + ≤ 1 permitted retry
  each. On fallback runs (the identity search finds no pages on the domain) the
  allocation is identity + fallback + overview + growth = 4, and the leadership search
  is skipped. **≤ 1 MoltSets company call + ≤ 1 people call** + ≤ 1 retry each, and
  people calls only after a company match. No exploratory re-queries after an empty
  result, except the single bounded identity fallback; post-identity Exa concurrency
  is capped at 2.
- Usage/quota figures are displayed only when the provider actually returns them —
  never invented.
- Live smoke tests are manual and opt-in only: one approved Exa identity search ran
  on 2026-10-16 (≈ $0.007 of free credit); no MoltSets call has been made.

## Known limitations and open gates

- **Public evidence may be incomplete.** `unavailable` means "not supported in this
  run", not "the company has none" — a missing signal is not proof of absence.
- **MoltSets signup/access is unconfirmed**, and the people/company search tools may
  be alpha. Endpoint paths and schemas are isolated in
  `backend/app/providers/moltsets.py` and must be re-checked against the current
  official docs before the first live call (see `docs/provider-notes.md`).
- **The GTM opportunity is a hypothesis, not purchase intent.** Funding, hiring, or
  product launches do not prove intent to buy; the UI labels the section "Inferred
  hypothesis".
- **Naming gates:** the brief spells the provider "Moldset" (MoltSets is the assumed
  service), and the second reference product is named only by description (Clay is an
  unconfirmed guess). Both are recorded; neither blocks any code.
- **Provider text is untrusted data.** It is length-capped and rendered as text only,
  but a hostile page could still contain misleading claims — evidence is quoted, not
  endorsed.
- **Single-company scope:** one request researches one company; no bulk processing,
  history, or persistence (by design).
- **Unverified assumptions** (full list in `docs/provider-notes.md`): retry-hint
  formats and the 10 s retry budget are implementation choices; `costDollars` is taken
  verbatim; `exa_invalid_response` / `moltsets_invalid_response` are internal codes
  for unparseable 200 bodies.
- The security audit passes: no key values or provider URLs in logs, error bodies, or
  the built frontend bundle — only variable names in user-facing recovery hints (see
  `backend/tests/test_security_audit.py`).

## Demo checklist

1. Start the backend in mock mode (above); confirm `GET http://localhost:8000/health`
   returns `{"status":"ok"}`.
2. Start the frontend (`npm run dev`) and open <http://localhost:3000>.
3. Submit the mock scenarios: `example-robotics.test` (full report + banner +
   "Sample" tags), `Example Ambiguous` (unresolved, no enrichment),
   `no-evidence.test` ("no evidence for this domain"), `moltsets-error.test` (partial
   report, Exa evidence kept), any unknown company (nothing invented).
4. Open the raw JSON panel and confirm `mock: true` / `data_mode: "mock"` appear.
5. Optional live run — only with an approved key and free credits: set `EXA_API_KEY`
   (and optionally `MOLTSETS_API_KEY`), remove the mock env vars, restart the backend,
   and run **one** search.

### What has not been live-tested

- **One live Exa call** (2026-10-16): a single approved identity search
  (`"Stripe company"`, scoped to `stripe.com`) through the backend adapter — HTTP 200,
  5 hits, one call, no retry, ≈ $0.007. The full four-search report path remains
  fixture-tested; the mock path is locally tested.
- **The identity fallback (search 1b) is fixture-tested only.** The bounded fallback
  path has not been exercised against live Exa — whether a category-less,
  domain-scoped search resolves a domain the company index misses is unconfirmed until
  an approved smoke test.
- **No live MoltSets call** has been made. Signup/access is unconfirmed; the adapter
  is fixture-tested and exercised via `MOLTSETS_MODE=mock` only.
- Remaining provider behavior (error codes, retry metadata, other query types and
  response-shape variants) stays **docs-verified (assumptions)** until a deliberate,
  approved smoke test exercises it.
- The manual browser journey (keyboard pass, 320 px layout) is tracked as a separate
  acceptance step; the automated UI tests and the local mock-mode API flow pass.
