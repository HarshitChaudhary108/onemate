# Outmate Prototype — Implementation Plan

**Version:** 1.0  
**Date:** 16 October 2026  
**Inputs:** `spec.md` v1.1 (binding product contract), `tech.md` v1.1 (technical design)  
**Delivery class:** Time-boxed prototype / proof of concept

---

## 1. Purpose and authority

This plan turns `spec.md` and `tech.md` into an ordered build sequence. For each step it lists the concrete files, tasks, tests, and exit criteria.

- **Precedence:** `spec.md` > `tech.md` > this plan. If this plan conflicts with either document, follow the document and correct this plan.
- **Verification claims:** "fixture-tested", "locally tested", and "live-tested" mean different things. Never report one as another.
- **Working style:** make small, reviewable changes. Get a working request-to-report path before any visual polish.

## 2. Baseline and assumptions

### 2.1 Current state (Gate 0 reconnaissance result)

- `D:\onemate` contains only `docs/spec.md` and `docs/tech.md`.
- There is no git repository, no frontend or backend code, no manifests, and no environment files.
- No committed secrets exist.
- **Conclusion:** this is a greenfield scaffold of the architecture selected in the spec. There are no existing conventions to preserve.

### 2.2 Selected tooling (greenfield defaults from spec §5 / tech §2)

| Area | Choice |
|---|---|
| Frontend | Next.js App Router, TypeScript (strict), React, Tailwind CSS, `lucide-react` icons, Zod (form validation and response narrowing) |
| Frontend tests | Vitest + `@testing-library/react` + `jsdom` |
| Backend | Python 3.11+, FastAPI, Pydantic v2, `pydantic-settings`, `httpx`, `uvicorn` |
| Backend tests | `pytest`, `pytest-asyncio`, `respx` (httpx mocking) |
| Backend quality | `ruff` (format + lint), `mypy` |
| Package managers | `npm` (frontend), `pip` + `pyproject.toml` with a `[dev]` extra (backend) |

### 2.3 Open gates (must not block the prototype)

| Gate | Default handling |
|---|---|
| "Moldset" vs **MoltSets** | Build the MoltSets adapter. Do not claim a live integration until Outmate confirms the provider or a supplied key validates. |
| MoltSets endpoint paths/schemas | Before any live call, check the current official docs and the account's allowed-tool schema. Until then, keep endpoint paths and field mapping isolated in `providers/moltsets.py` and fixture-tested. |
| Exa auth header (`Authorization: Bearer` vs `x-api-key`) and response shape | Check the current docs at implementation time and use one scheme consistently. Fixtures follow the documented shape. |
| Comparative reference product (Clay) | Record the caveat in the README. This does not block any code. |

### 2.4 Repository hygiene

- Run `git init` at `D:\onemate`.
- Add a root `.gitignore` covering:
  - `.env`, `.env.*` (except `.env.example`)
  - `.venv/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`
  - `node_modules/`, `.next/`, `coverage/`
- Keep `docs/` as the home for `spec.md`, `tech.md`, and this plan.

## 3. Target repository layout

```text
D:\onemate
├─ .gitignore
├─ README.md
├─ docs/
│  ├─ spec.md
│  ├─ tech.md
│  └─ implementation-plan.md
├─ backend/
│  ├─ pyproject.toml
│  ├─ .env.example
│  ├─ app/
│  │  ├─ __init__.py
│  │  ├─ main.py                 # FastAPI app, CORS, exception handlers, router
│  │  ├─ config.py               # pydantic-settings: keys, modes, CORS_ORIGINS
│  │  ├─ api/research.py         # POST /api/research
│  │  ├─ schemas/research.py     # ResearchRequest
│  │  ├─ schemas/report.py       # Status, Source, Field[T], Report, ...
│  │  ├─ research/orchestrator.py# run_research(request, deps)
│  │  ├─ research/queries.py     # centralized Exa query builders
│  │  ├─ normalize/domain.py     # input/domain normalization
│  │  ├─ normalize/report.py     # precedence, source attachment, dedupe, hypothesis
│  │  ├─ policy/status.py        # overall_status + missing_fields
│  │  ├─ providers/types.py      # Protocols + typed adapter results
│  │  ├─ providers/exa.py        # httpx Exa adapter
│  │  ├─ providers/exa_mock.py   # deterministic Exa fake (tests / explicit demo)
│  │  ├─ providers/moltsets.py   # httpx MoltSets adapter (company + people only)
│  │  ├─ providers/moltsets_mock.py
│  │  ├─ errors/provider_error.py# ProviderError + code table
│  │  └─ utils/safe_url.py       # http/https-only URL sanitization
│  └─ tests/
│     ├─ conftest.py             # network guard, fixtures loader, fake deps
│     ├─ fixtures/               # exa_*.json, moltsets_*.json (synthetic)
│     ├─ test_domain.py
│     ├─ test_status.py
│     ├─ test_schemas.py
│     ├─ test_normalize_report.py
│     ├─ test_exa_adapter.py
│     ├─ test_moltsets_adapter.py
│     ├─ test_orchestrator.py
│     └─ test_research_api.py
└─ frontend/
   ├─ package.json
   ├─ .env.example
   ├─ next.config.ts, tsconfig.json, tailwind/postcss config, vitest.config.ts
   ├─ app/
   │  ├─ layout.tsx
   │  ├─ globals.css
   │  └─ page.tsx
   ├─ components/
   │  ├─ research-form.tsx
   │  ├─ report-header.tsx
   │  ├─ overview-section.tsx
   │  ├─ growth-signals.tsx
   │  ├─ decision-makers.tsx
   │  ├─ opportunity-hypothesis.tsx
   │  ├─ sources-panel.tsx
   │  ├─ source-list.tsx         # per-claim evidence
   │  ├─ status-badge.tsx        # text + icon, never colour-only
   │  ├─ missing-fields.tsx
   │  ├─ provider-status.tsx
   │  ├─ mock-banner.tsx
   │  ├─ report-skeleton.tsx
   │  └─ json-viewer.tsx
   ├─ lib/
   │  ├─ api/client.ts
   │  ├─ schema/report.ts        # TS types + Zod narrowing schema
   │  ├─ schema/request.ts       # Zod form schema
   │  └─ utils/format.ts
   └─ tests/
      ├─ client.test.ts
      ├─ report-schema.test.ts   # contract test vs backend schema snapshot
      ├─ research-form.test.tsx
      └─ report-states.test.tsx
```

Create a file only when it has content. If a smaller equivalent is simpler (for example, merging `exa_mock.py` into test fixtures), prefer it and note the choice in the README.

## 4. Milestones

Each milestone lists its tasks, then its tests, then an **exit criterion**. Do not start a milestone until the previous one meets its exit criterion.

---

### M0 — Scaffold and baseline (tech Gate 0)

**Tasks**
1. Run `git init`. Add `.gitignore` (§2.4).
2. Backend:
   - Create `backend/pyproject.toml`:
     - Runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `httpx`.
     - `[project.optional-dependencies].dev`: `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`.
     - Config sections for ruff, mypy, and pytest (`asyncio_mode = "auto"`).
   - Pin versions published at least 7 days ago. Do not use floating `latest` ranges.
3. Backend `app/config.py` (`pydantic-settings`):
   - Settings: `EXA_API_KEY`, `EXA_MODE` (`live`|`mock`, default `live`), `MOLTSETS_API_KEY`, `MOLTSETS_MODE` (`live`|`mock`|`off`, default `live`), `MOLTSETS_USER_AGENT` (default `outmate-prototype/1.0`), `CORS_ORIGINS` (comma list, default `http://localhost:3000`).
   - Wrap secrets in `SecretStr`.
4. Backend `app/main.py`:
   - FastAPI app with `CORSMiddleware` restricted to the explicit `CORS_ORIGINS` list. Never use `*`.
   - `GET /health` returns `{"status":"ok"}`.
5. `backend/.env.example` per tech §3.1, plus a documented `EXA_MODE=live`. Placeholders only.
6. Frontend:
   - Scaffold with `create-next-app` (TypeScript, App Router, Tailwind, ESLint, no `src/`).
   - Add `zod` and `lucide-react`.
   - Dev deps: `vitest`, `@vitejs/plugin-react`, `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`.
   - Scripts: `dev`, `build`, `lint`, `typecheck` (`tsc --noEmit`), `test` (`vitest run`).
7. `frontend/.env.example`: `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` only.

**Exit**
- `uvicorn app.main:app` starts, and `/health` returns 200.
- `npm run dev` serves the default page.
- `ruff check`, `mypy app`, `npm run lint`, and `npm run typecheck` all pass on the scaffold.

---

### M1 — Contracts first (tech Gate 1 / spec Gate 1)

**Tasks**

1. `schemas/research.py` — `ResearchRequest`:
   - `company_name: str | None` (≤200 chars), `domain: str | None` (≤253 chars after parsing), `research_objective: str | None` (≤1000 chars).
   - Strings are trimmed. Blank becomes `None`. Control characters are rejected.
   - A `model_validator` requires at least one of `company_name` / `domain`.
   - `extra="forbid"`; strict string types.
2. `normalize/domain.py` — `normalize_domain(raw) -> NormalizedDomain(original, host, compare_key)`:
   - Accept a bare host or an `http(s)://` URL. Reject other schemes, embedded credentials, and missing hosts.
   - Drop the path, query, and fragment. Lowercase. Strip a trailing dot. Convert IDNs to punycode via `idna`/`encodings.idna`.
   - Strip `www.` for the comparison key only.
   - Reject: `localhost`, IP literals that are private, reserved, loopback, or link-local (`ipaddress`), single-label hosts, labels longer than 63 characters, and invalid label characters.
   - No DNS lookups and no fetching.
3. `schemas/report.py`:
   - `Status = Literal["verified","provider-reported","inferred","unavailable"]`.
   - `ProviderName = Literal["Exa","MoltSets"]`.
   - `Source`: `provider`, `mock: bool`, `retrieved_at: datetime` (required); `url`, `title`, `published_date`, `highlight`, `query` (optional). A validator runs `url` through `safe_url`.
   - `Field[T]` (generic): `value: T | None`, `status`, `sources: list[Source]`, optional `note`. Validators:
     - `verified` requires ≥1 source with a URL.
     - `value is None` / empty list implies `unavailable`.
   - `GrowthSignal`: `type` (`hiring|funding|expansion|leadership_change|product_launch|other`), `summary`, `date`, `sources`.
   - `DecisionMaker`: `name`, `title`, `seniority`, `profile_url`, `status`, `sources`, `mock`.
   - `ContactInfo`: `person_name`, `business_email`, `email_risk_grade`, `email_last_validated`, `status`, `sources`, `mock`, plus fixed explanatory text: "deliverability signal, not identity proof".
   - `GtmHypothesis`: `summary`, `rationale: list[{point, sources}]`, `caveat`.
   - `CompanyIdentity`: `resolved: bool`, `company_name: Field[str]`, `domain: Field[str]`, `note`.
   - `ProviderState = Literal["ok","partial","error","not_configured","mock","disabled","not_found","quota_exhausted"]`.
   - `ProviderSummary`: `state`, `mock`, `calls: int`, `usage: dict | None` (only when returned), `note`.
   - `ReportError`: `provider` (`Exa|MoltSets|Application`), `stage` (`identity|overview|growth|leadership|objective|enrichment|normalization|api`), `code`, `message`, `retryable`, `request_id`.
   - `Report`:
     - `schema_version: Literal["1.0"]`, `data_mode: Literal["live","mock","mixed"]`, `request` (echo), `company_identity`.
     - Factual fields: `description`, `industry`, `target_market`, `employee_size`, `growth_signals`, `decision_makers`, `contact_information`.
     - `potential_gtm_opportunity`, `overall_status`, `missing_fields: list[str]`, `errors`, `warnings: list[str]`, `providers: {exa, moltsets}`, `request_id`, `generated_at`.
   - `ErrorResponse` (non-200 bodies): `error: {code, message, request_id, details?}`.
4. `policy/status.py`:
   - `FACTUAL_FIELDS = (description, industry, target_market, employee_size, growth_signals, decision_makers, contact_information)`.
   - `compute_overall_status(report)` implements spec §7 exactly (unresolved or no values → `unavailable`; any inferred → `inferred`; any provider-reported → `provider-reported`; else `verified`). `potential_gtm_opportunity` is excluded.
   - `compute_missing_fields(report)` returns the factual fields whose status is `unavailable`.
   - `compute_data_mode(sources, providers)` returns `live`/`mock`/`mixed`, based on providers that actually contributed.
5. `utils/safe_url.py`: allow `http`/`https` only, no credentials, and a valid host. Otherwise return `None`.
6. Frontend `lib/schema/report.ts`:
   - Zod schemas mirroring the Pydantic models, with TS types inferred via `z.infer`.
   - `lib/schema/request.ts` is the form schema (same limits, at least one field).
7. **Contract drift check:**
   - A backend test writes `Report.model_json_schema()` to `backend/tests/fixtures/report.schema.json` and checks that the committed copy matches.
   - The frontend `report-schema.test.ts` confirms that the field names and enums in that JSON schema match the Zod schema.
   - Also parse a backend-generated sample report fixture with the Zod schema.
8. Synthetic fixtures in `backend/tests/fixtures/`:
   - Exa: identity hit, identity ambiguous, overview, growth (news with dates), leadership, empty.
   - MoltSets: company, people, `not_found`, error bodies.
   - All fixture companies and people are obviously fictional (e.g. "Example Robotics Ltd", `example-robotics.test`, "Sample Person (fixture)").

**Tests**
- `test_domain.py`: whitespace, casing, protocol, `www`, trailing dot, path/query/fragment, IDN, malformed, unsupported scheme, credentials, localhost, private/reserved IP, single label, empty, length limits.
- `test_status.py`: unresolved identity, no factual values, all verified, verified + provider-reported mix, inferred factual field, missing optional fields still yielding a useful status, GTM excluded.
- `test_schemas.py`: request validation errors, `verified` without a URL source is rejected, mock is not a status, round-trip of a sample report, schema snapshot.
- Frontend: `report-schema.test.ts`.

**Exit**
- `pytest`, `ruff`, and `mypy` pass.
- `npm run typecheck` and `npm test` pass.
- The contract and status tests are green.

---

### M2 — Provider adapters (tech Gate 2)

**Tasks**

1. `errors/provider_error.py`: a `ProviderError(Exception)` with `provider`, `stage`, `code`, `message` (safe), `retryable`, `retry_after: float | None`, and `http_status`. Add `to_report_error(request_id)`. Messages never include keys, headers, or raw payloads.
2. `providers/types.py`:
   - Protocols `ExaProvider.search(ExaSearchRequest) -> ExaSearchResult`, `MoltSetsProvider.search_company(domain)` and `search_people(company_domain, seniority, limit=5)`.
   - Typed result models: `ExaHit(url, title, published_date, highlights, score)`, `ExaSearchResult(hits, query, usage, calls)`, `MoltSetsCompany(...)`, `MoltSetsPerson(...)`, `MoltSetsCompanyResult(found, company, calls, usage)`, `MoltSetsPeopleResult(people, calls, usage)`.
   - Every result carries `mock: bool` and `calls` (actual HTTP attempts including the retry).
3. `providers/exa.py` (`HttpExaProvider`):
   - Shared `httpx.AsyncClient` with an explicit `httpx.Timeout(30.0, connect=5.0)`.
   - `POST https://api.exa.ai/search` with `type: "auto"`, `numResults ≤ 5`, and highlights requested. No full text and no summaries.
   - `ExaSearchRequest` validator: date filters only with `category="news"`. A date filter combined with `company`/`people` categories raises `invalid_request` locally, so no call is made.
   - **Single retry boundary** (adapter only):
     - Retry once on timeout, network error, 5xx, or 429. Honor `Retry-After` if it fits within the remaining deadline. Otherwise use a bounded backoff (≤2 s).
     - Never retry 400/401/402/422.
   - Error code map:

| HTTP / condition | code | retryable |
|---|---|---|
| 400 | `exa_bad_request` | no |
| 401 | `exa_invalid_key` | no |
| 402 | `exa_credits_exhausted` | no |
| 422 | `exa_invalid_request` | no |
| 429 | `exa_rate_limited` | yes, once |
| timeout / network | `exa_timeout` / `exa_network` | yes, once |
| 5xx | `exa_server_error` | yes, once |
| missing key (live) | `exa_not_configured` | no, no call |

   - Empty `results` gives `ExaSearchResult(hits=[])`, not an error. Usage/cost metadata is kept only when present.
4. `providers/exa_mock.py`: `FixtureExaProvider` maps query intent to fixture files and marks `mock=True`. It is used only in tests or when `EXA_MODE=mock`.
5. `providers/moltsets.py` (`HttpMoltSetsProvider`):
   - Endpoint paths and field names are isolated in constants, labelled "verify against official docs/account schema before live use".
   - Every request sends the key header and a non-empty `User-Agent`.
   - `search_company(domain)`:
     - Only takes a normalized confirmed domain.
     - HTTP 200 + `status:"not_found"` → `found=False`.
     - Results are cached in a process-local `dict` keyed by the comparison key (company only).
   - `search_people(...)`: seniority `["C Suite","VP","Owner"]`, `limit = min(limit, 5)` enforced in code. Results are never cached.
   - Business emails are never logged.
   - Error code map (branch on body code first, then HTTP status):

| Condition | code | retryable |
|---|---|---|
| 200 + `not_found` | (not an error) → `found=False` | — |
| 401 | `moltsets_invalid_key` | no |
| 402 | `moltsets_payment_required` | no |
| 403 missing User-Agent | `moltsets_missing_user_agent` | no |
| 403 plan restricted / inactive | `moltsets_plan_restricted` | no |
| 404 unknown tool | `moltsets_tool_not_found` | no |
| 422 | `moltsets_invalid_input` | no |
| 429 burst with retry time | `moltsets_rate_limited` | yes, once |
| 429 `fair_use_limit_exceeded`, no reset | `moltsets_quota_exhausted` | no |
| timeout / network | `moltsets_timeout` / `moltsets_network` | yes, once |
| 5xx | `moltsets_server_error` | yes, once |

6. `providers/moltsets_mock.py`: `FixtureMoltSetsProvider` returns synthetic fixtures with `mock=True`. People are labelled "Sample", with emails on the reserved `.test`/`example.com` domains.
7. Provider factory in `config.py` or `api/research.py`. It builds deps from settings and never falls back to mocks when keys are missing.

**Tests**
- `conftest.py`: an autouse fixture that blocks real network access (respx `assert_all_mocked`, plus patching sockets if needed). The test environment clears `EXA_API_KEY`/`MOLTSETS_API_KEY`.
- `test_exa_adapter.py`:
  - Request shape: URL, auth header, `type:auto`, `numResults ≤ 5`, highlights requested.
  - Success parse; empty results; missing date/highlights.
  - 400/401/402/422 make exactly one call. 429 with `Retry-After` makes 2 calls. 429 without `Retry-After` makes 2 calls with bounded backoff. Timeout makes 2 calls. 500 makes 2 calls and then raises.
  - Date filter combined with company/people category raises with zero calls.
  - Errors never contain the key.
- `test_moltsets_adapter.py`:
  - Company/people success; 200 `not_found`.
  - Each error row above. `User-Agent` is always sent. People limit is capped at 5.
  - Company cache hit makes no second call. People results are never cached.
  - `email_risk_grade` never alters identity/status semantics.

**Exit:** the adapter fixture tests pass. The network guard proves that no live calls happen. Missing credentials never enable mock mode.

---

### M3 — Orchestrator and API (tech Gate 3 / spec Gate 2)

**Tasks**

1. `research/queries.py` — pure builders, unit-tested. There are four logical searches at most:
   1. `identity_query(name, domain)`: company identity / official site (`category="company"` allowed, no date filter). If the domain is known, prefer `includeDomains=[domain]` plus a name query.
      - **1b.** `identity_fallback_query(name, domain)`: broader fallback identity search (same subject and `includeDomains` as search 1; `category` omitted). Runs only when search 1 finds no pages on the requested domain, and counts toward the four-search budget; when it is used, the leadership search is skipped.
   2. `overview_query(identity)`: about / products / customers / market.
   3. `growth_query(identity)`: `category="news"`, `startPublishedDate` = today − 12 months; terms covering hiring, funding, expansion, leadership, launch.
   4. `leadership_query(identity, objective)`: leadership / executives. If an objective is supplied, fold objective terms into query 2 or 4 to stay within four searches. No separate objective search is made when the objective is blank.
2. Identity resolution rule (conservative, documented in the README):
   - **Resolved** when ≥1 Exa hit's URL host (comparison key) equals the normalized input domain. For name-only input, the hit must also show a consistent title/name match, and the top hits must agree on a single host.
   - **Ambiguous** when hits point to more than one plausible host for a name-only input, when there are no hits, or when the supplied name and domain conflict.
   - The resolved name/domain are `verified` only with the supporting hit as a source. The user's input alone is never a citation.
3. `research/orchestrator.py` — `async def run_research(req, deps) -> Report`. `deps` holds `exa`, `moltsets | None`, `moltsets_mode`, `clock`, and `request_id`. Flow:
   1. Normalize the input and initialize the builder, counters, warnings, and errors.
   2. If Exa is not configured and not mocked, raise `ResearchUnavailable("exa_not_configured")`. The API maps this to 503.
   3. Run the identity search. If it fails with a non-retryable error and there is no evidence, raise `ResearchUnavailable` (→ 502). Otherwise record the error.
   4. If identity is unresolved, return an unresolved report: identity-dependent fields unavailable, **no MoltSets calls**, and a note explaining why.
   5. Run searches 2–4 under `asyncio.Semaphore(2)` with `gather(return_exceptions=True)`. Convert `ProviderError` into `ReportError`. Re-raise any other exception, which goes to the 500 handler.
   6. MoltSets branch:
      - `off` → `disabled`, 0 calls.
      - `mock` → fixture adapter, `mock`.
      - Live without a key → `not_configured`, 0 calls, no fixtures.
      - Live → company search, then people (≤5) only if the company was found. A failure is kept as `error`/`quota_exhausted` and the Exa data is preserved.
   7. Call `normalize/report.py` to build the fields, then compute statuses, `missing_fields`, `overall_status`, `data_mode`, and provider summaries (`calls` = actual attempts).
   8. `Report.model_validate(...)` and return.
4. `normalize/report.py`:
   - **Evidence selection:** a field cites only the hits whose highlight text supports it (keyword/phrase checks per field). Sources are attached per field or item and are never copied report-wide.
   - **Description:** a concise sentence from overview/about highlights, quoted or lightly trimmed, never generated filler. `verified` with the source, otherwise `unavailable`.
   - **Industry:** an Exa phrase if clear (`verified`), otherwise the MoltSets industry (`provider-reported`). Keep both sources when they are compatible. On a conflict, add a warning and mark it `unavailable` if the conflict is material.
   - **Target market:** only an explicit segment phrase from company material. Otherwise `unavailable`.
   - **Employee size:** a dated public statement (`verified`), otherwise the exact MoltSets count/range (`provider-reported`). Do not add precision that isn't in the source.
   - **Growth signals:** one item per supporting news hit, typed by keyword, with a date and its own sources. If there are none, the note says "No supported signals found in this run."
   - **Decision-makers:** Exa-supported name+role pairs (`verified`) merged with MoltSets people (`provider-reported`). Deduplicate by normalized name + company domain + role and merge the sources.
   - **Contact information:** MoltSets-returned business email / risk grade / validation date only (`provider-reported`), with the deliverability caveat. Never infer anything.
   - **GTM hypothesis** (`build_hypothesis(objective, fields)`):
     - A deterministic template that runs only when the objective plus ≥1 sourced signal (growth/overview) exist.
     - Cautious wording ("may", "could", "potential fit"). Each rationale point carries the sources of the signal it uses. A fixed caveat: "not confirmed buying intent."
     - Status `inferred`. Otherwise `value=None`, `unavailable`, with an explanatory note.
   - All strings from providers are treated as data. They are length-capped and never interpreted.
5. `api/research.py` + `main.py`:
   - `POST /api/research` → 200 `Report`.
   - `RequestValidationError` → 422 with a safe `{error:{code:"validation_error", message, details:[{field,msg}]}}`.
   - `ResearchUnavailable` → 502/503 `ErrorResponse`.
   - Unhandled → 500 with a generic message and `request_id`. The traceback is logged server-side only.
   - Middleware adds an `X-Request-ID` header and `Cache-Control: no-store` to every response.
   - Logging uses the `request_id`. Emails and keys are never logged.

**Tests**
- `test_normalize_report.py`:
  - Null/optional fields, empty results, date parsing, unsafe URLs dropped.
  - Duplicate people merged with their sources. Sources attached to the right item.
  - Industry conflict → warning/unavailable.
  - Hypothesis present (inferred, cautious, sourced) and insufficient-evidence path.
  - Mock flag propagation; `data_mode` live/mock/mixed.
- `test_orchestrator.py` (fake providers with call recorders):
  - Ambiguous identity → MoltSets calls = 0.
  - Growth search fails, overview kept, error listed.
  - MoltSets 500 → Exa fields kept, provider `error`.
  - Missing MoltSets key → `not_configured`, no fixture data.
  - `off` → `disabled`.
  - Exa search count ≤ 4 (plus retries counted). No objective → no objective terms. Concurrency never exceeds 2.
  - Deterministic `missing_fields`/`overall_status`/counts with a frozen clock.
- `test_research_api.py` (`httpx.AsyncClient` + `ASGITransport`, deps overridden):
  - 200 schema-valid report; 422 on empty/invalid input.
  - 503 when Exa is not configured; 500 is generic with a request ID and no traceback.
  - `no-store` header; CORS allowed for a listed origin and refused for others.
  - Error bodies contain no key/`Authorization` strings.

**Exit:** the successful, empty, ambiguous, partial, and failure paths are tested and green. The endpoint works locally with `EXA_MODE=mock MOLTSETS_MODE=mock`.

---

### M4 — Frontend journey and visual quality (tech Gate 4 / spec Gate 3)

**Tasks (in order — get the main journey working before polish)**
1. `lib/api/client.ts` — `researchCompany(input): Promise<Result>` with a discriminated union:
   - `{kind:"report", report}`: a 200 response narrowed by Zod. A parse failure becomes `kind:"invalid_response"`.
   - `{kind:"validation", fields}` for 422.
   - `{kind:"api_error", code, message, requestId}` for 5xx.
   - `{kind:"network"}` for a fetch rejection.
   - Base URL from `process.env.NEXT_PUBLIC_API_BASE_URL`. No provider URLs exist anywhere in the frontend.
2. `components/research-form.tsx`:
   - One "Company name or website" input (an input that looks like a URL/domain is sent as `domain`, otherwise `company_name`), plus an optional objective textarea.
   - Zod validation with field-level messages linked via `aria-describedby`.
   - Clickable example chip `stripe.com` that fills the field but never auto-submits.
   - Submit is disabled while a request is in flight.
3. `app/page.tsx` — a client state machine: `idle | loading | report | error`.
   - Loading shows the truthful generic message "Researching company…" (`aria-live="polite"`) and `report-skeleton.tsx`. No fake stages or percentages.
4. Report rendering (each section hides absent values instead of showing empty placeholders):
   - `report-header.tsx`: name, canonical domain, overall `StatusBadge`, coverage summary (fields available / total, data mode).
   - `mock-banner.tsx`: a persistent, prominent "Mock/sample data" banner whenever `data_mode !== "live"` or any source has `mock`.
   - `overview-section.tsx`: description, industry, target market, employee size, each with a `StatusBadge` and an inline `SourceList`.
   - `growth-signals.tsx`: dated items with a summary, type, and per-item sources. The empty state reads "No supported signals found in this run."
   - `decision-makers.tsx`: a compact list/table (stacked cards below `sm`). Columns are hidden when no row has a value. A provider/source label is shown on each row, and mock rows carry a "Sample" tag.
   - `opportunity-hypothesis.tsx`: an "Inferred hypothesis" label, rationale with evidence references, and a caveat. Unavailable shows the note.
   - `sources-panel.tsx`: a global list (title, domain, published date, retrieved time, highlight, link, mock marker) in the right rail on `lg+`. It supplements the inline sources and never replaces them.
   - `missing-fields.tsx` + `provider-status.tsx`: missing fields, provider state, call counts, usage (only when returned), and errors with recovery hints.
   - Unresolved state: explains what was searched and why enrichment was skipped.
   - Error state: an accessible `role="alert"` block with a safe message and the request ID. The form keeps its values.
5. `status-badge.tsx`: a text label plus an icon for each status (`Verified`, `Provider-reported`, `Inferred`, `Unavailable`). "Verified" is rendered only if the field has ≥1 inspectable source with a URL.
6. `source-list.tsx` / links: only `http(s)` URLs, `target="_blank" rel="noopener noreferrer"`, shown with their domain.
7. Visual system (`globals.css` + Tailwind theme):
   - Warm off-white page background (e.g. `#FAF8F5`), near-white surfaces, slate/navy text, one indigo accent.
   - Subtle borders, modest radii, restrained shadows. A system font stack or `next/font` Inter.
   - Visible focus rings, `prefers-reduced-motion` respected, no horizontal overflow at 320px.
8. Last step: `json-viewer.tsx`, a collapsed `<details>` with pretty JSON and a "Copy JSON" button (accessible name, success announcement).

**Tests (Vitest + Testing Library, fetch mocked)**
- `client.test.ts`: 200/422/5xx/network/invalid-shape mapping. Uses the env base URL.
- `research-form.test.tsx`: empty submit shows field error. Domain vs name detection. Example chip doesn't submit. Button disabled while loading.
- `report-states.test.tsx`, rendered from backend-generated fixture JSON:
  - Success, partial (MoltSets error), unresolved, no-evidence, mock (banner + sample tags), API error.
  - Sources rendered next to the right growth item/person.
  - No "Verified" badge without a source.
  - Copy JSON calls the clipboard.

**Exit:** with both services running in mock mode, a user can submit a request and inspect a fixture-backed report with honest provenance labels. The frontend lint, typecheck, and tests pass.

---

### M5 — Reliability, security, documentation (tech Gate 5 / spec Gate 4)

**Tasks**
1. Error and log audit:
   - Grep tests confirm that the key values, `Authorization`, `x-api-key`, tracebacks, and emails never appear in responses or log records (`caplog`).
2. CORS:
   - Confirm the explicit allowlist parsing.
   - Confirm that the default excludes `*`.
3. Secrets:
   - Confirm that no provider key appears in any `NEXT_PUBLIC_*` variable.
   - Run `npm run build` and grep `.next/` for `EXA`/`MOLTSETS` key names to confirm they are absent.
4. `.env.example` files contain variable names and safe placeholders only.
5. Root `README.md` covers spec §13 items 1–9:
   - Scope and non-goals; architecture flow diagram; prerequisites.
   - Install, run, test, and build commands for both apps.
   - Environment variables.
   - Mock vs live modes and how mock is surfaced.
   - Illustrative request/response (labelled fixture-backed).
   - Exa/MoltSets integration notes, status semantics, and retry/error behavior.
   - Free-tier limits and the no-unapproved-paid-usage rule.
   - Open gates (§2.3) and known limitations.
6. Add `AGENTS.md` with the verified build/test commands.

**Exit:** all suites pass or blockers are recorded. No secret is exposed.

---

### M6 — Final acceptance (tech Gate 6 / spec Gate 5)

1. Run the full command matrix (§7) and record the exact outcomes.
2. Manual UI journey in mock mode (`EXA_MODE=mock`, `MOLTSETS_MODE=mock`):
   - Success, unresolved (ambiguous fixture), and partial (MoltSets error fixture) runs.
   - Keyboard-only pass; 320px viewport check.
3. Run a live Exa smoke test **only** if a key is intentionally configured and the user approves. Make one search. Record the date, response shape, usage metadata, and credits consumed, without secrets.
4. Make live MoltSets calls only after access is confirmed. Otherwise report "unconfigured / access-blocked / mock-only".
5. Review the diff for:
   - unrelated edits;
   - duplicate research logic in Next.js;
   - fake progress;
   - silent mocks;
   - unsupported claims;
   - accidental paid calls.
6. Write the handoff described in tech §14: Implemented / Commands run / Provider verification / Cost discipline / Known limitations / Demo instructions. Never say "production-ready", "fully integrated", or "live verified" unless that was actually exercised.

## 5. Design reference tables

### 5.1 Field precedence → status

| Field | Primary source | Fallback | Status outcomes |
|---|---|---|---|
| Identity name/domain | Exa hit with a host match | — | `verified` / `unavailable` (blocks MoltSets) |
| Description | Exa about/product highlight | — | `verified` / `unavailable` |
| Industry | Exa explicit phrase | MoltSets industry | `verified` / `provider-reported` / `unavailable` (conflict) |
| Target market | Explicit Exa company segment | — | `verified` / `unavailable` |
| Employee size | Dated Exa statement | MoltSets exact count/range | `verified` / `provider-reported` / `unavailable` |
| Growth signals | Exa news hits (per item) | — | `verified` / `unavailable` |
| Decision-makers | Exa name+role (per item) | MoltSets people ≤5 | outer status summarizes items |
| Contact information | MoltSets business contact only | — | `provider-reported` / `unavailable` |
| GTM opportunity | Objective + sourced signals | — | `inferred` / `unavailable` (excluded from overall) |

Outer status for collections: `verified` if every item is verified, `provider-reported` if any item is provider-reported, `unavailable` if empty.

### 5.2 MoltSets mode → behavior

| `MOLTSETS_MODE` | Key | Calls | Provider state | Data |
|---|---|---|---|---|
| `off` | any | 0 | `disabled` | enrichment unavailable |
| `mock` | any | fixture only | `mock` | `mock: true`, `data_mode` mock/mixed |
| `live` | missing | 0 | `not_configured` | enrichment unavailable, no fixtures |
| `live` | present | company → people (if found) | `ok` / `not_found` / `partial` / `error` / `quota_exhausted` | live |

The same principle applies to `EXA_MODE`: in `live` mode a missing key means 503 `exa_not_configured`, never mocks.

### 5.3 HTTP response mapping

| Situation | HTTP |
|---|---|
| Report built (including partial/unresolved/no-evidence) | 200 |
| Request validation failed | 422 |
| Exa not configured | 503 |
| Identity search failed with no usable evidence | 502 |
| Unexpected exception | 500 (generic, `request_id`) |

### 5.4 Budget and concurrency

- ≤4 logical Exa searches per report, plus at most one retry each.
- Concurrency ≤2 after identity.
- MoltSets: ≤1 company + ≤1 people call (≤5 records), plus at most one permitted retry.
- No exploratory re-queries after an empty result, except the single bounded identity fallback (search 1b); when it is used, the leadership search is skipped so the report still makes ≤ 4 logical searches.

## 6. Test matrix (requirement → test location)

| Requirement (spec §10 / tech §12) | Test |
|---|---|
| Domain normalization cases | `backend/tests/test_domain.py` |
| Overall status algorithm | `backend/tests/test_status.py` |
| Schema validity, verified-needs-source, mock≠status | `backend/tests/test_schemas.py` |
| Normalization: nulls, empty, dates, URL safety, dedupe, source attachment, conflicts | `backend/tests/test_normalize_report.py` |
| Mock propagation and `data_mode` | `test_normalize_report.py`, `test_orchestrator.py` |
| GTM hypothesis present / insufficient | `test_normalize_report.py` |
| Error sanitization (keys, headers, traces, emails) | `test_research_api.py`, adapter tests (`caplog`) |
| Exa 400/401/402/422/429±Retry-After/timeout/500, empty, missing fields, filter validation | `backend/tests/test_exa_adapter.py` |
| MoltSets success, not_found, 401/402/403×2/404/422/429×2/timeout/500, UA header, ≤5 people, cache rules, email risk semantics | `backend/tests/test_moltsets_adapter.py` |
| Ambiguous identity blocks MoltSets; partial survival; no silent mocks; deterministic counts; budget/concurrency | `backend/tests/test_orchestrator.py` |
| API validation, output schema, status codes, headers, CORS | `backend/tests/test_research_api.py` |
| No network in tests | `backend/tests/conftest.py` guard |
| TS/Pydantic contract drift | `frontend/tests/report-schema.test.ts` + backend schema snapshot |
| Client result mapping | `frontend/tests/client.test.ts` |
| Form validation and submit behavior | `frontend/tests/research-form.test.tsx` |
| Partial/empty/mock/error states, per-item evidence, verified badge rule, copy JSON | `frontend/tests/report-states.test.tsx` |
| Keyboard access, 320px layout, real browser journey | Manual (M6) |
| Live Exa / MoltSets | Manual, opt-in only (M6) |

## 7. Command matrix

**Backend** (`cd backend`, venv active):

```bash
pip install -e ".[dev]"
ruff format --check .
ruff check .
mypy app
pytest
uvicorn app.main:app --reload --port 8000
```

**Frontend** (`cd frontend`):

```bash
npm install
npm run lint
npm run typecheck
npm test
npm run build
npm run dev
```

**Mock demo:** set `EXA_MODE=mock` and `MOLTSETS_MODE=mock` in `backend/.env`, start both services, then open `http://localhost:3000`.

## 8. Acceptance traceability (spec §12)

| Acceptance item | Milestone | Evidence |
|---|---|---|
| Submit name and/or domain with optional objective | M1, M4 | request schema tests, form tests, manual journey |
| Server calls Exa when configured and keeps source metadata | M2, M3 | Exa adapter tests; optional live smoke |
| Versioned schema with field-level statuses | M1 | schema tests, contract test |
| MoltSets only after identity + access + key | M3 | orchestrator tests |
| Missing MoltSets access leaves the app functional, fields unavailable | M3 | orchestrator tests, mode table |
| Mock opt-in, in metadata, conspicuous in the UI | M2–M4 | mock propagation tests, mock UI test |
| No fabricated facts/contacts/signals/counts/intent | M3 | normalization + hypothesis tests, diff review |
| Claims carry URLs; provider-only stays provider-reported | M1, M3 | verified-needs-source validator, normalization tests |
| Unresolved identity blocks enrichment | M3 | orchestrator test |
| Partial results survive provider failure; errors and missing fields listed | M3, M4 | orchestrator + UI partial tests |
| Keys backend-only, not in files/logs; `.env.example` placeholders | M0, M5 | sanitization tests, build grep, review |
| Tests cover normalization, status, adapter errors, mocks, partials | M1–M3 | §6 matrix |
| Responsive, accessible, polished UI with all states | M4, M6 | UI tests, manual 320px/keyboard pass |
| README complete | M5 | README review against spec §13 |
| Final report distinguishes live vs fixture testing | M6 | handoff |

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| MoltSets access/schema unconfirmed | Isolated constants and adapter; fixture-only tests; `not_configured` default; README blocker entry. |
| Exa auth/response shape drift | One adapter boundary; check the docs before live use; fixtures follow the documented shape; parse defensively (optional fields). |
| Weak identity heuristic yields false matches | Conservative host-match rule; ambiguity blocks enrichment; identity cites the matching hit. |
| Free-credit burn | Hard budget caps in code; network-blocked tests; live smoke opt-in with one call. |
| Evidence-to-field mismatch (over-claiming "verified") | Per-field highlight checks; schema validator rejects `verified` without a URL source; UI badge rule. |
| Contract drift between Pydantic and Zod | Schema snapshot + frontend contract test. |
| CORS / env misconfiguration in the demo | Explicit defaults for localhost:3000/8000; README troubleshooting. |
| Prompt-injection text in retrieved pages | Content treated as inert strings; no LLM; length caps; rendered as text (no `dangerouslySetInnerHTML`). |
| Scope creep into polish before the main journey works | Milestone ordering; JSON viewer and visual refinements last. |

## 10. Explicitly out of scope

The following are out of scope:
- Bulk lists, CSV import, CRM sync, outreach/email sequences, Slack, LinkedIn scraping, and ad audiences.
- A database, auth, billing, job queues, production deployment, and multi-tenancy.
- Paid enrichment/phone endpoints, contact inference, and numeric lead/intent scores.
- A separate LLM provider and autonomous multi-agent orchestration.
- Streaming/stage events (the loading state stays generic and truthful).
