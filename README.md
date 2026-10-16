# Outmate — AI GTM Research & Enrichment Prototype

A time-boxed prototype: enter a company name or website, optionally state a research
objective, and get a readable, evidence-backed GTM research report. A Next.js UI talks
to a Python/FastAPI backend that runs one deterministic research pipeline over the
**Exa** search API and optional **MoltSets** enrichment.

> **Verification status:** everything in this repository is **fixture-tested** and the
> mock path is **locally tested**. One approved live Exa smoke search was made on
> 2026-10-16 (identity query only, ≈ $0.007 of free credit) and its response was
> inspected; **no live MoltSets call has ever been made** — see
> [What has not been live-tested](#what-has-not-been-live-tested).

---

## 1. What this prototype does — and deliberately does not do

### It does

- Accept **one company** (name and/or domain, plus an optional objective) and return a
  versioned JSON report with per-field status, per-claim source evidence, missing
  fields, provider states, and structured errors.
- Resolve company identity through Exa before doing anything domain-dependent
  (ambiguous or unsupported identities stop enrichment and say why).
- Run focused Exa searches (identity, overview/market, growth/news, leadership) with a
  hard per-report budget, then optional MoltSets company + people enrichment
  (≤ 5 people, C-suite / VP / Owner) **only** after a resolved identity.
- Build a conservative GTM opportunity **hypothesis** from the user's objective plus
  cited signals — never generic sales copy, never fabricated intent.
- Survive partial failure: one provider failing keeps the other provider's evidence
  and lists the error.
- Offer an explicit, visible **mock mode** for demos and tests, with deterministic
  synthetic fixtures and honest `mock` provenance everywhere.

### It deliberately does not do

- No bulk lists, CSV import, CRM sync, outreach sequences, cold email, LinkedIn
  automation, or ad-audience sync.
- No database, user accounts, billing, queues, background jobs, or multi-tenant code.
  The only cache is a process-local MoltSets **company lookup** cache keyed by domain.
- No separate LLM provider; descriptions and the opportunity hypothesis are composed
  from captured evidence with deterministic templates.
- No paid endpoints: no phone lookup, no personal-email lookup, no contact inference,
  no numeric buying-intent/lead-score.
- No autonomous multi-agent orchestration. The workflow is a deterministic pipeline.
- No production deployment infrastructure.

---

## 2. Architecture and workflow

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
  │    ├─ Exa adapter: company identity search (1 call, always first)
  │    ├─ Exa adapter: overview / market, growth/news, leadership
  │    │    (3 calls, concurrency ≤ 2, objective folded into overview)
  │    ├─ MoltSets adapter: company lookup — only after the identity gate
  │    └─ MoltSets adapter: ≤ 5 people — only after a company match
  ├─ normalization / precedence / source attachment app/normalize/report.py
  ├─ field status + missing fields + overall status app/policy/status.py
  └─ validated versioned JSON report                app/schemas/report.py
  │
  ▼
Next.js renders the evidence-backed report, provider states, and mock banner
```

Boundary rules that hold in the code:

- The browser **never** calls Exa or MoltSets directly, and there is no research
  endpoint in Next.js — FastAPI owns validation, provider calls, retries, and status
  semantics. The frontend only knows `NEXT_PUBLIC_API_BASE_URL`.
- Every source is attached to the exact field or item it supports; a source elsewhere
  in the report never makes a field `verified`.
- Provider responses are parsed at the adapter boundary and treated as untrusted data
  (length-capped strings, http(s)-only URLs, no HTML injection, no instruction
  following).

Repository layout:

```text
backend/    FastAPI app (app/) + pytest suite (tests/) + synthetic fixtures
frontend/   Next.js App Router UI (app/, components/, lib/) + Vitest suite (tests/)
docs/       spec.md, tech.md, implementation-plan.md, provider-notes.md
```

---

## 3. Prerequisites

- **Python 3.11+** (verified with 3.13.5) and `pip`.
- **Node.js 20+** and npm (verified with Node 24.19.0 / npm 11.17.0).
- Windows, macOS, or Linux shell. Commands below show Windows activation first, then
  Git Bash.

---

## 4. Install, run, test, build

### Backend (`backend/`)

```powershell
cd backend
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows cmd.exe:
.venv\Scripts\activate.bat
# Windows Git Bash:
source .venv/Scripts/activate
# macOS / Linux:
#   source .venv/bin/activate

pip install -e ".[dev]"
ruff format --check .
ruff check .
mypy app
pytest
uvicorn app.main:app --port 8000     # GET http://localhost:8000/health -> {"status":"ok"}
```

### Frontend (`frontend/`)

```powershell
cd frontend
npm install
npm run lint
npm run typecheck     # tsc --noEmit
npm test              # vitest run
npm run build
npm run dev           # http://localhost:3000
```

The frontend needs the backend running for actual research requests; without it the UI
shows a truthful "could not reach the research service" error.

### Mock-mode demo (no keys, no provider calls)

```powershell
# Terminal 1 — backend in explicit mock mode
cd backend
.venv\Scripts\Activate.ps1
$env:EXA_MODE="mock"; $env:MOLTSETS_MODE="mock"
uvicorn app.main:app --port 8000
# Git Bash instead of PowerShell:
#   EXA_MODE=mock MOLTSETS_MODE=mock uvicorn app.main:app --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev
```

Then open <http://localhost:3000>. Alternatively, set `EXA_MODE=mock` and
`MOLTSETS_MODE=mock` in `backend/.env` (env vars take precedence over the file).
Mock mode is always disclosed — see [section 6](#6-mock-vs-live-mode).

---

## 5. Environment variables

Copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to
`frontend/.env` and fill in what you need. **Only variable names and safe placeholders
are committed; `.env` files are gitignored. Never commit real keys.**

| Variable | App | Default | Meaning |
|---|---|---|---|
| `EXA_API_KEY` | backend | *(blank)* | Exa API key. Blank in live mode means *not configured* (HTTP 503); it **never** enables mock mode. |
| `EXA_MODE` | backend | `live` | `live` or `mock`. `mock` uses deterministic fixtures and is always disclosed in the response. |
| `MOLTSETS_API_KEY` | backend | *(blank)* | MoltSets API key. Blank in live mode means enrichment `not_configured` (no call, no fixtures). |
| `MOLTSETS_MODE` | backend | `live` | `live`, `mock`, or `off` (`off` reports the provider as disabled and makes no call). |
| `MOLTSETS_USER_AGENT` | backend | `outmate-prototype/1.0` | Non-empty User-Agent sent on every MoltSets request (the API rejects missing/blank UAs). |
| `CORS_ORIGINS` | backend | `http://localhost:3000` | Comma-separated explicit allowlist of frontend origins. `*` and empty values are rejected at startup. |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | `http://localhost:8000` | Public base URL of the FastAPI backend. This is **not a secret**; provider keys never appear in any `NEXT_PUBLIC_*` variable. |

Provider secrets exist only in backend code/environment. They never appear in logs,
error bodies, the frontend bundle, or fixtures (the M5 audit tests enforce this).

---

## 6. Mock vs live mode

Each provider has its own mode; mock mode is **explicit opt-in** and can never be
triggered by a missing key or a failed live call.

| Mode setting | Key present | Calls made | Provider state | Data |
|---|---|---|---|---|
| `EXA_MODE=live` | yes | real Exa searches | `ok` / `partial` / `error` | live |
| `EXA_MODE=live` | no | none | — (request fails) | HTTP 503 `exa_not_configured` |
| `EXA_MODE=mock` | any | fixture only | `ok`, `mock: true` | `data_mode` mock/mixed |
| `MOLTSETS_MODE=off` | any | none | `disabled` | enrichment unavailable |
| `MOLTSETS_MODE=mock` | any | fixture only | `mock` | `mock: true`, `data_mode` mock/mixed |
| `MOLTSETS_MODE=live` | no | none | `not_configured` | enrichment unavailable, **no fixtures** |
| `MOLTSETS_MODE=live` | yes | company → people (if found) | `ok` / `not_found` / `partial` / `error` / `quota_exhausted` | live |

How mock is surfaced (all three, always):

1. **Response metadata** — every mock source and provider summary carries `mock: true`,
   and `data_mode` becomes `mock` (or `mixed` when live and mock data are combined).
2. **Provider state** — mock runs report `providers.moltsets.state: "mock"` (and Exa
   `mock: true`).
3. **UI** — a persistent "Mock / sample data" banner is shown whenever
   `data_mode !== "live"` or any attached source is mock; mock people/contact rows get
   a visible "Sample" tag; fixture people are named "Sample … (fixture)" with emails
   only on reserved `.test`/`example.com` domains.

### Deterministic mock scenarios

With `EXA_MODE=mock MOLTSETS_MODE=mock`, scenario selection is driven by the input
(substring match, case-insensitive):

| Input | What happens | Result |
|---|---|---|
| `example-robotics.test` (optionally with the name "Example Robotics Ltd") | Exa returns the identity/overview/growth/leadership fixtures; MoltSets returns the synthetic company + 2 people | Full mock report; mock banner; `data_mode: "mock"` |
| an input containing `ambiguous` (e.g. `Example Ambiguous`) | Exa loads the multi-host ambiguous identity fixture | Unresolved identity report (no name-matching hits); **no MoltSets calls**; the note explains what was searched |
| `no-evidence.test` | every Exa search returns empty results | Unresolved report: "no evidence for this domain" |
| `moltsets-error.test` | Exa returns the success fixtures; the mock MoltSets provider raises `moltsets_server_error` | Partial report: Exa evidence preserved, error listed, enrichment fields unavailable |
| anything else (e.g. name-only "Example Robotics", `unknown-nobody.test`) | every Exa search returns empty results | Unresolved report ("no public-source hits matched the supplied company name" / "no evidence for this domain"); **nothing is invented** |

The multi-host ambiguity note ("top name-matching hits point to more than one host") is
exercised by the orchestrator tests and the committed
`backend/tests/fixtures/reports/unresolved.json` snapshot; through the mock adapter's
scenario routing, inputs containing `ambiguous` end unresolved because the fixture's
candidate hosts do not name-match the literal input.

Mock mode never falls back silently in either direction: a live failure stays a live
error, and missing credentials never switch to fixtures.

---

## 7. Example request and response

**Illustrative, fixture-backed.** The request below is the one recorded inside
`backend/tests/fixtures/reports/mock.json`; the response is that committed snapshot
reproduced **verbatim** (produced by the real pipeline against fixture providers, not a
live run). A full success variant with an objective is committed at
`backend/tests/fixtures/reports/success.json`; partial/unresolved/no-evidence variants
live next to it.

Example request (from the mock-mode demo):

```bash
curl -s http://localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"domain": "example-robotics.test"}'
```

<details>
<summary><strong>Example response — <code>backend/tests/fixtures/reports/mock.json</code> (click to expand)</strong></summary>

```json
{
  "company_identity": {
    "company_name": {
      "note": null,
      "sources": [
        {
          "highlight": "Example Robotics Ltd builds autonomous robots for warehouse picking.",
          "mock": true,
          "provider": "Exa",
          "published_date": "2025-11-02",
          "query": "example-robotics.test company",
          "retrieved_at": "2026-10-16T12:00:00Z",
          "title": "Example Robotics Ltd — Warehouse Automation",
          "url": "https://example-robotics.test/"
        },
        {
          "highlight": "Example Robotics Ltd is a fictional fixture company. It designs and operates warehouse robotics systems.",
          "mock": true,
          "provider": "Exa",
          "published_date": "2026-06-01",
          "query": "example-robotics.test company",
          "retrieved_at": "2026-10-16T12:00:00Z",
          "title": "About Example Robotics Ltd",
          "url": "https://example-robotics.test/about"
        }
      ],
      "status": "verified",
      "value": "Example Robotics Ltd"
    },
    "domain": {
      "note": null,
      "sources": [
        {
          "highlight": "Example Robotics Ltd builds autonomous robots for warehouse picking.",
          "mock": true,
          "provider": "Exa",
          "published_date": "2025-11-02",
          "query": "example-robotics.test company",
          "retrieved_at": "2026-10-16T12:00:00Z",
          "title": "Example Robotics Ltd — Warehouse Automation",
          "url": "https://example-robotics.test/"
        },
        {
          "highlight": "Example Robotics Ltd is a fictional fixture company. It designs and operates warehouse robotics systems.",
          "mock": true,
          "provider": "Exa",
          "published_date": "2026-06-01",
          "query": "example-robotics.test company",
          "retrieved_at": "2026-10-16T12:00:00Z",
          "title": "About Example Robotics Ltd",
          "url": "https://example-robotics.test/about"
        }
      ],
      "status": "verified",
      "value": "example-robotics.test"
    },
    "note": null,
    "resolved": true
  },
  "contact_information": {
    "note": null,
    "sources": [
      {
        "highlight": null,
        "mock": true,
        "provider": "MoltSets",
        "published_date": null,
        "query": null,
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": null,
        "url": null
      },
      {
        "highlight": null,
        "mock": true,
        "provider": "MoltSets",
        "published_date": null,
        "query": null,
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": null,
        "url": null
      }
    ],
    "status": "provider-reported",
    "value": [
      {
        "business_email": "sample.person@example-robotics.test",
        "email_last_validated": "2026-09-20",
        "email_risk_grade": "B",
        "email_risk_note": "deliverability signal, not identity proof",
        "mock": true,
        "person_name": "Sample Person (fixture)",
        "sources": [
          {
            "highlight": null,
            "mock": true,
            "provider": "MoltSets",
            "published_date": null,
            "query": null,
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": null,
            "url": null
          }
        ],
        "status": "provider-reported"
      },
      {
        "business_email": "sample.founder@example-robotics.test",
        "email_last_validated": "2026-09-22",
        "email_risk_grade": "A",
        "email_risk_note": "deliverability signal, not identity proof",
        "mock": true,
        "person_name": "Sample Founder (fixture)",
        "sources": [
          {
            "highlight": null,
            "mock": true,
            "provider": "MoltSets",
            "published_date": null,
            "query": null,
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": null,
            "url": null
          }
        ],
        "status": "provider-reported"
      }
    ]
  },
  "data_mode": "mock",
  "decision_makers": {
    "note": null,
    "sources": [
      {
        "highlight": "Sample Person (fixture) leads engineering as VP of Engineering at Example Robotics.",
        "mock": true,
        "provider": "Exa",
        "published_date": "2026-08-10",
        "query": "Example Robotics Ltd example-robotics.test leadership executives CEO founder",
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": "Team — Example Robotics Ltd",
        "url": "https://example-robotics.test/team"
      },
      {
        "highlight": "Sample Person (fixture), VP of Engineering at Example Robotics Ltd.",
        "mock": true,
        "provider": "Exa",
        "published_date": null,
        "query": "Example Robotics Ltd example-robotics.test leadership executives CEO founder",
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": "Sample Person (fixture) — Profile",
        "url": "https://profiles.example-robotics.test/sample-person"
      },
      {
        "highlight": null,
        "mock": true,
        "provider": "MoltSets",
        "published_date": null,
        "query": null,
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": null,
        "url": null
      },
      {
        "highlight": null,
        "mock": true,
        "provider": "MoltSets",
        "published_date": null,
        "query": null,
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": null,
        "url": null
      }
    ],
    "status": "provider-reported",
    "value": [
      {
        "mock": true,
        "name": "Sample Person",
        "profile_url": null,
        "seniority": null,
        "sources": [
          {
            "highlight": "Sample Person (fixture) leads engineering as VP of Engineering at Example Robotics.",
            "mock": true,
            "provider": "Exa",
            "published_date": "2026-08-10",
            "query": "Example Robotics Ltd example-robotics.test leadership executives CEO founder",
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": "Team — Example Robotics Ltd",
            "url": "https://example-robotics.test/team"
          },
          {
            "highlight": "Sample Person (fixture), VP of Engineering at Example Robotics Ltd.",
            "mock": true,
            "provider": "Exa",
            "published_date": null,
            "query": "Example Robotics Ltd example-robotics.test leadership executives CEO founder",
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": "Sample Person (fixture) — Profile",
            "url": "https://profiles.example-robotics.test/sample-person"
          }
        ],
        "status": "verified",
        "title": "VP of Engineering"
      },
      {
        "mock": true,
        "name": "Sample Person (fixture)",
        "profile_url": "https://linkedin.com/in/sample-person-fixture",
        "seniority": "VP",
        "sources": [
          {
            "highlight": null,
            "mock": true,
            "provider": "MoltSets",
            "published_date": null,
            "query": null,
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": null,
            "url": null
          }
        ],
        "status": "provider-reported",
        "title": "VP of Engineering"
      },
      {
        "mock": true,
        "name": "Sample Founder (fixture)",
        "profile_url": "https://linkedin.com/in/sample-founder-fixture",
        "seniority": "C Suite",
        "sources": [
          {
            "highlight": null,
            "mock": true,
            "provider": "MoltSets",
            "published_date": null,
            "query": null,
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": null,
            "url": null
          }
        ],
        "status": "provider-reported",
        "title": "Chief Executive Officer"
      }
    ]
  },
  "description": {
    "note": null,
    "sources": [
      {
        "highlight": "Example Robotics Ltd builds warehouse robots for logistics operators.",
        "mock": true,
        "provider": "Exa",
        "published_date": "2026-06-01",
        "query": "Example Robotics Ltd example-robotics.test overview products customers market",
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": "About Example Robotics Ltd",
        "url": "https://example-robotics.test/about"
      }
    ],
    "status": "verified",
    "value": "Example Robotics Ltd builds warehouse robots for logistics operators."
  },
  "employee_size": {
    "note": null,
    "sources": [
      {
        "highlight": null,
        "mock": true,
        "provider": "MoltSets",
        "published_date": null,
        "query": null,
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": null,
        "url": null
      }
    ],
    "status": "provider-reported",
    "value": "140"
  },
  "errors": [],
  "generated_at": "2026-10-16T12:00:00Z",
  "growth_signals": {
    "note": null,
    "sources": [
      {
        "highlight": "Example Robotics announced a fictional Series A round to fund expansion.",
        "mock": true,
        "provider": "Exa",
        "published_date": "2026-07-12",
        "query": "Example Robotics Ltd example-robotics.test hiring funding expansion leadership launch",
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": "Example Robotics raises fixture funding round",
        "url": "https://news.example-robotics.test/funding-2026"
      }
    ],
    "status": "verified",
    "value": [
      {
        "date": "2026-07-12",
        "sources": [
          {
            "highlight": "Example Robotics announced a fictional Series A round to fund expansion.",
            "mock": true,
            "provider": "Exa",
            "published_date": "2026-07-12",
            "query": "Example Robotics Ltd example-robotics.test hiring funding expansion leadership launch",
            "retrieved_at": "2026-10-16T12:00:00Z",
            "title": "Example Robotics raises fixture funding round",
            "url": "https://news.example-robotics.test/funding-2026"
          }
        ],
        "summary": "Example Robotics announced a fictional Series A round to fund expansion.",
        "type": "funding"
      }
    ]
  },
  "industry": {
    "note": null,
    "sources": [
      {
        "highlight": null,
        "mock": true,
        "provider": "MoltSets",
        "published_date": null,
        "query": null,
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": null,
        "url": null
      }
    ],
    "status": "provider-reported",
    "value": "Information Technology"
  },
  "missing_fields": [],
  "overall_status": "provider-reported",
  "potential_gtm_opportunity": {
    "note": "Insufficient evidence to form a hypothesis.",
    "sources": [],
    "status": "unavailable",
    "value": null
  },
  "providers": {
    "exa": {
      "calls": 4,
      "mock": true,
      "note": null,
      "state": "ok",
      "usage": {
        "contents": {
          "highlights": 0.0025
        },
        "search": {
          "neural": 0.0025
        },
        "total": 0.005
      }
    },
    "moltsets": {
      "calls": 2,
      "mock": true,
      "note": null,
      "state": "mock",
      "usage": {
        "fair_use": {
          "records_remaining_1w": 300421,
          "records_remaining_5h": 89997,
          "records_reset_1w": "2026-10-21T08:00:00Z",
          "records_reset_5h": "2026-10-16T17:00:00Z"
        },
        "tokens_remaining": 95,
        "tokens_used": 2
      }
    }
  },
  "request": {
    "company_name": null,
    "domain": "example-robotics.test",
    "research_objective": null
  },
  "request_id": "req-fixture-0006",
  "schema_version": "1.0",
  "target_market": {
    "note": null,
    "sources": [
      {
        "highlight": "The company sells to mid-size distribution centers in Europe.",
        "mock": true,
        "provider": "Exa",
        "published_date": "2026-06-01",
        "query": "Example Robotics Ltd example-robotics.test overview products customers market",
        "retrieved_at": "2026-10-16T12:00:00Z",
        "title": "About Example Robotics Ltd",
        "url": "https://example-robotics.test/about"
      }
    ],
    "status": "verified",
    "value": "mid-size distribution centers in Europe"
  },
  "warnings": []
}
```

</details>

Reading guide:

- `schema_version: "1.0"` — the response contract version.
- `data_mode` — `live`, `mock`, or `mixed`, derived from the data that actually
  contributed.
- `request` — echo of the validated input.
- `company_identity` — resolution result; `resolved: false` blocks all enrichment.
- Every factual field is a wrapper: `value` + `status` + `sources` (+ optional `note`).
  A `verified` value always carries at least one source with an HTTP(S) URL; MoltSets
  sources legitimately have `url: null` and their values stay `provider-reported`.
- `providers` — per-provider state, `mock` flag, **actual call count** (including
  retries), and usage metadata only when the provider returned it.
- `errors` — structured, display-safe provider/application errors with `retryable` and
  the `request_id`.
- `missing_fields` — factual fields left `unavailable` in this run.

---

## 8. Provider integration notes

### 8.1 Exa (live adapter: `backend/app/providers/exa.py`)

- Endpoint `POST https://api.exa.ai/search`, authenticated with the `x-api-key` header
  (one documented scheme, used consistently). Key read only from `EXA_API_KEY` in the
  backend process.
- Request shape: `type: "auto"`, `numResults ≤ 5`, `contents: {"highlights": true}`.
  No full-page text, no generated summaries. Optional `includeDomains` for the
  identity search; publication-date filters only with `category: "news"` (a date filter
  combined with any other category is rejected locally with `exa_invalid_request`
  before any call).
- Timeouts: 30 s per request, 5 s connect. One retry boundary in the adapter (never in
  the orchestrator).
- Empty `results` is a normal no-evidence outcome, not an error.
- `costDollars` is preserved verbatim as usage metadata when present; nothing is
  invented when it is absent.

**Exa error-code table**

| HTTP / condition | Internal code | Retryable |
|---|---|---|
| 400 | `exa_bad_request` | no |
| 401 | `exa_invalid_key` | no |
| 402 | `exa_credits_exhausted` | no |
| 422 | `exa_invalid_request` | no |
| 429 | `exa_rate_limited` | yes, once |
| timeout / network error | `exa_timeout` / `exa_network` | yes, once |
| 5xx | `exa_server_error` | yes, once |
| missing key (live mode) | `exa_not_configured` | no — no call is made |
| unparseable 200 body (internal) | `exa_invalid_response` | no |

### 8.2 MoltSets (live adapter: `backend/app/providers/moltsets.py`)

- Base URL `https://api.moltsets.com/api/v1/tools/`; only the `search_companies` and
  `search_people` tools are ever called. Phone, personal-email, and every other tool
  are out of scope.
- `Authorization: Bearer <key>` on every request, plus a non-empty `User-Agent`
  (`MOLTSETS_USER_AGENT`); a blank UA raises `moltsets_missing_user_agent` locally
  before any call.
- Company lookup by the normalized confirmed domain; **cached in process memory**
  keyed by the domain comparison key (errors are never cached). People searches are
  never cached. Business emails are never logged.
- People search: seniority `["C Suite", "VP", "Owner"]`, `limit` hard-capped at 5 in
  code (the free plan allows max 5 records per call).
- HTTP 200 + `status: "not_found"` is a normal miss (`found=False`), not an error.
  Error mapping branches on the response **body code first**, then HTTP status.
- `business_email_risk_score` is a deliverability signal, never identity proof; the
  fixed note "deliverability signal, not identity proof" travels with every contact.

**MoltSets error-code table**

| Condition | Internal code | Retryable |
|---|---|---|
| 200 + `status: "not_found"` | *(not an error)* → `found=False` | — |
| 401 `unauthorized` | `moltsets_invalid_key` | no |
| 402 `insufficient_tokens` (or `insufficient_phone_tokens`) | `moltsets_payment_required` | no |
| 403 `forbidden` (missing User-Agent) | `moltsets_missing_user_agent` | no |
| 403 `plan_restricted` / `account_inactive` | `moltsets_plan_restricted` | no |
| 404 `tool_not_found` | `moltsets_tool_not_found` | no |
| 422 `invalid_input` | `moltsets_invalid_input` | no |
| 429 `rate_limited` / `fair_use_requests_exceeded` with retry time | `moltsets_rate_limited` | yes, once |
| 429 `fair_use_limit_exceeded` **without** a reset time | `moltsets_quota_exhausted` | no |
| timeout / network error | `moltsets_timeout` / `moltsets_network` | yes, once |
| 5xx | `moltsets_server_error` | yes, once |
| missing key (live mode) | `moltsets_not_configured` | no — no call is made |
| unparseable 200 body (internal) | `moltsets_invalid_response` | no |

### 8.3 Retry, timeout, and partial-result policy

- At most **one retry per request**, only for transient failures: timeout, network
  error, 5xx, or a rate limit with a usable retry time.
- `Retry-After` (Exa) and `metadata.retry_after` (MoltSets, with the header as
  fallback) are honored within a 10 s implementation budget; a longer required wait
  means no retry, so the UI is never left hanging. Retries without a hint use a fixed
  1.0 s bounded backoff.
- 400/401/402/422/403/404 failures are never retried, and credit/fair-use exhaustion
  without a reset time is never retried.
- One provider failing never erases the other provider's evidence: Exa failures are
  recorded per stage (overview/growth/leadership), MoltSets failures degrade the
  enrichment fields only, and the report lists every structured error.

### 8.4 Status semantics (spec section 7)

Exactly four field statuses exist:

| Status | Meaning |
|---|---|
| `verified` | Directly supported by cited public-source evidence; the evidence must actually support the displayed value, and at least one source must carry an HTTP(S) URL. |
| `provider-reported` | Returned by a structured provider (e.g. MoltSets) without independent cited public-source evidence for that value. |
| `inferred` | A reasoned conclusion from cited evidence and the objective — used by the GTM hypothesis. |
| `unavailable` | Missing, unsupported, or unsafe to resolve confidently. |

`mock` is **not** a status — it is separate provenance metadata (`mock: true` on
sources/providers plus the top-level `data_mode`). `providers.*.state` is also separate
(`ok`, `partial`, `error`, `not_configured`, `mock`, `disabled`, `not_found`,
`quota_exhausted`).

`overall_status` is computed from the seven factual fields only (the GTM hypothesis is
always excluded):

1. `unavailable` if the identity is unresolved or no factual field has a value;
2. otherwise `inferred` if any valued factual field is `inferred`;
3. otherwise `provider-reported` if any factual field is `provider-reported`;
4. otherwise `verified`.

Missing optional fields do not downgrade an otherwise useful report.

### 8.5 Identity-resolution rules

- **Domain supplied:** resolved iff ≥ 1 Exa hit's host matches the normalized domain
  (subdomains count, `www.` ignored). If a name is supplied too, at least one
  domain-matching hit must also name-match; otherwise the run is ambiguous
  ("supplied name and domain do not agree"). No domain-matching hit at all →
  unresolved ("no evidence for this domain").
- **Name only:** the top 5 name-matching hits must all share a single host. Multiple
  hosts → ambiguous; no matching hits → unresolved.
- A resolved identity's name/domain are `verified` with the supporting hit(s) as
  sources; **the user's input alone is never a citation**.
- An unresolved or ambiguous identity returns a report immediately: identity-dependent
  fields stay `unavailable`, **MoltSets is never called**, and the note explains why.

### 8.6 HTTP response mapping

| Situation | HTTP |
|---|---|
| Report built (including partial / unresolved / no-evidence) | 200 |
| Request validation failed | 422 (safe `{error:{code,message,request_id,details}}`, no input echo) |
| Exa not configured | 503 `exa_not_configured` |
| Identity search failed with no usable evidence | 502 |
| Unexpected internal failure | 500 (generic message + `request_id`; traceback logged server-side only) |

Every response carries `X-Request-ID` and `Cache-Control: no-store`.

---

## 9. Cost discipline and free-tier limits

- **Never incur unapproved paid usage.** Do not add a payment method, upgrade a plan,
  or call paid endpoints for this prototype. Automated tests never call providers (a
  network guard blocks non-loopback sockets and all HTTP is mocked).
- MoltSets free tier (per the supplied research): **100 lifetime search records**,
  **max 5 records per call** — the adapter caps people searches at 5 in code.
- Per-report provider budget:
  - **≤ 4 logical Exa searches** (identity, overview/market, growth/news, leadership;
    the objective is folded into the overview query, and a blank objective adds no
    query), plus at most one permitted retry each;
  - **≤ 1 MoltSets company call + ≤ 1 MoltSets people call**, plus at most one
    permitted retry; people calls happen only after a company match;
  - no exploratory re-queries after an empty result.
- Post-identity Exa concurrency is capped at 2, so an objective-heavy request cannot
  burst.
- Usage/quota figures are displayed **only when the provider actually returns them**
  (`costDollars`, MoltSets `metadata`). No quota balance is ever invented.
- Live smoke tests are manual and opt-in only. One approved Exa identity search was run
  on 2026-10-16 (one call, no retry, ≈ $0.007 of free credit); no MoltSets call has
  been made.

---

## 10. Known limitations and open gates

- **Public evidence may be incomplete.** A field stays `unavailable` when the run
  cannot support it; "No supported signals found in this run" is not a claim that a
  company has no growth signals.
- **MoltSets signup/access is unconfirmed.** The adapter is built and fixture-tested,
  but no account access has been validated. Live MoltSets calls are gated behind a key
  the operator supplies.
- **The MoltSets people/company search tools may be alpha.** Endpoint paths, request
  fields, and response shapes are isolated in `backend/app/providers/moltsets.py` and
  must be re-checked against the current official docs and the account's allowed-tool
  schema before the first live call (see `docs/provider-notes.md`).
- **The GTM opportunity is a hypothesis, not purchase intent.** Funding, hiring, or
  product launches do not prove intent to buy; the UI labels the section "Inferred
  hypothesis" and carries a caveat.
- **The "Moldset"/MoltSets naming gate.** The brief spells the provider "Moldset"; the
  supplied investigation identifies MoltSets as the likely intended service. This must
  be confirmed with Outmate (or by validating supplied credentials) before claiming a
  live integration.
- **Clay reference caveat.** The brief names Orange Slice as the primary reference and
  says a second product was shared but does not name it. The research docs flag
  **Clay** as an unconfirmed guess. Recorded here; it does not block any code.
- **Provider text is untrusted data.** It is length-capped and rendered as text only
  (no HTML injection), but a hostile page could still contain misleading claims —
  evidence is quoted, not endorsed.
- **Single-company scope.** One request researches one company; no bulk processing,
  history, or persistence (by design).
- The M5 security audit (sentinel keys/emails, error bodies, logs, CORS, bundle grep)
  passes; see `backend/tests/test_security_audit.py`. The only "sensitive-string"
  matches in the built frontend bundle are **variable names** in user-facing recovery
  hints (e.g. "Add EXA_API_KEY to backend/.env") and the provider name "MoltSets" in
  status labels — never key values, and no provider URLs.

### Unverified assumptions (from `docs/provider-notes.md`)

Everything below is assumed behavior, **not confirmed by provider documentation**.
Re-check before the first live call.

| Provider | Unverified assumption |
|---|---|
| Exa | `Retry-After` is assumed to arrive as an HTTP header carrying a delta in seconds. HTTP-date form is not handled; an unparsable value is treated as absent. |
| Exa | `costDollars` is taken verbatim as `usage` metadata when present; individual breakdown keys are not required. |
| Exa | `RETRY_WAIT_BUDGET_SECONDS = 10.0` is an implementation choice (not a provider limit): if `Retry-After` exceeds 10 s the adapter does not wait and raises `exa_rate_limited` after the first attempt, so the UI is never left hanging. Retries without `Retry-After` use a fixed 1.0 s bounded backoff (under the 2 s cap in the plan). |
| Exa | HTTP statuses outside the plan's error table (e.g. 403, 404, other 4xx, or a non-200 2xx) are mapped to `exa_bad_request`, non-retryable. |
| Exa | `exa_invalid_response` (non-retryable) is an additional internal code for a 200 whose body cannot be parsed; it is not a documented Exa code. |
| MoltSets | `metadata.retry_after` (body) is treated as the primary retry hint; the HTTP `Retry-After` header is also read as a fallback. A 429 `fair_use_limit_exceeded` **with** a usable retry time is treated as retryable (docs say "wait `retry_after` seconds"); only without a retry time does it become `moltsets_quota_exhausted`. |
| MoltSets | The same `RETRY_WAIT_BUDGET_SECONDS = 10.0` budget rule applies (an implementation choice): a longer required wait means no retry. |
| MoltSets | `fair_use_requests_exceeded` is mapped to `moltsets_rate_limited` (retryable), `insufficient_phone_tokens` to `moltsets_payment_required` (unreachable — phone tools are never called). |
| MoltSets | A 403 whose body code is absent/unrecognized maps to `moltsets_plan_restricted` per the plan's disambiguation rule. |
| MoltSets | `moltsets_invalid_response` (non-retryable) is an additional internal code for unparseable 200 bodies. |
| MoltSets | Whether a 200 with `status: "ok"` but an empty `results.results` array can occur is unspecified; the adapter treats it as `found=False`. |

---

## 11. Demo checklist

1. Start the backend in mock mode (see [section 4](#4-install-run-test-build)) and
   confirm `GET http://localhost:8000/health` returns `{"status":"ok"}`.
2. Start the frontend (`npm run dev`) and open <http://localhost:3000>.
3. Submit the scenarios and check the honest labels:
   - `example-robotics.test` → full mock report; "Mock / sample data" banner visible;
     "Sample" tags on mock people.
   - `Example Ambiguous` → unresolved identity report; no enrichment; explanation
     shown.
   - `no-evidence.test` → unresolved; "no evidence for this domain".
   - `moltsets-error.test` → partial report; Exa evidence kept; the MoltSets error is
     listed with a recovery hint.
   - any unknown company/domain → nothing invented; fields explicitly unavailable.
4. Check the raw JSON panel (collapsed `<details>` with copy-to-clipboard) and confirm
   `mock: true` / `data_mode: "mock"` appear.
5. For a live run (optional, only with an approved key and free credits): set
   `EXA_API_KEY` (and optionally `MOLTSETS_API_KEY`), remove the mock env vars, restart
   the backend, and run **one** search. MoltSets live calls only after access is
   confirmed.

### What has not been live-tested

- **One live Exa call has been made** (2026-10-16): a single approved identity search
  (`"Stripe company"`, `category="company"`, `includeDomains=["stripe.com"]`) through
  the backend adapter — HTTP 200, 5 hits, one call, no retry, `costDollars` usage
  ≈ $0.007. The full four-search report path remains fixture-tested, and the mock path
  is locally tested; the recorded response shape is in `docs/provider-notes.md`.
- **No live MoltSets call** has been made. Signup/access is unconfirmed; the adapter is
  fixture-tested and locally tested through `MOLTSETS_MODE=mock` only.
- The remaining provider behavior — error codes, retry metadata parsing, and the other
  query types and response-shape variants — stays **docs-verified (assumptions)** until
  a deliberate, approved smoke test exercises it (see `docs/provider-notes.md` for the
  full list).
- The real-browser manual journey (keyboard pass, 320 px layout) is tracked as a
  separate acceptance step; the automated UI tests and the local mock-mode API flow
  pass.
