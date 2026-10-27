# Outmate — HubSpot Integration Plan

**Status:** planning document — no implementation yet
**Scope:** real OAuth-based HubSpot integration for the Outmate prototype (local run, HubSpot developer test account). Salesforce follows later by reusing this shape.
**Source of truth for this feature:** this document. (`docs/spec.md`, `docs/tech.md`, `docs/implementation-plan.md`, `docs/plan.md`, `frontend/docs/api-contract.md` are referenced elsewhere but absent from this clone.)

---

## 1. Why this integration exists

Outmate is the researcher; HubSpot is the sales team's system of record. Without an integration, reps must export Outmate's enrichment results by hand and re-import them into the CRM — stale data, duplicate records, and dropped research.

Two-way exchange fixes that:

- **CRM → Outmate:** existing companies/contacts come in for research and enrichment.
- **Outmate → CRM:** approved, evidence-backed findings go back onto the record the rep already works from.

Two-way does **not** mean either side blindly overwrites the other. Ownership is explicit per field (§9): HubSpot owns every standard property; Outmate owns only the `outmate_*` property namespace.

### Deliberately out of scope for v1

- Real-time/continuous sync (Phase 2 — webhooks + incremental pull)
- Deals, engagements, sequences, bidirectional contact write-back
- Multi-account/multi-portal connections (one HubSpot portal at a time)
- Auth on Outmate's own API (prototype rule unchanged — run locally or behind a trusted boundary)

---

## 2. Tech stack — what we need vs. what we already have

**Answer: mostly reuse.** The existing stack covers routing, async HTTP, storage, SSE, and the frontend client pattern. Two small backend dependencies are added; nothing else.

### Already in the repo — reused as-is

| Piece | Where | Used for |
|---|---|---|
| FastAPI router + `ErrorResponse` contract | `backend/app/main.py`, `app/api/*.py` | Integration endpoints + error codes |
| `httpx.AsyncClient` | `app/providers/exa.py` (pattern) | HubSpot REST calls, retry boundary |
| `aiosqlite` store, `user_version` guard | `app/sheets/db.py`, `repo.py` | Tokens, links, field map, push log |
| `normalize_domain().compare_key` | `app/normalize/domain.py` | Import dedupe alongside remote IDs |
| `SheetService.import_rows` + `SheetRunner.resolve_pending_identities` | `app/sheets/service.py`, `app/engine/runner.py` | Companies → sheet rows, then identity pass |
| `respx` | `backend/tests/` (dev dep) | Mocking `api.hubapi.com` in tests |
| zod mirror + `SheetResult` union client | `frontend/lib/schema/`, `lib/api/` | Frontend contract discipline |

### New backend dependencies (2)

| Dep | Why | Pin rule |
|---|---|---|
| `Authlib` | OAuth2 client — `AsyncOAuth2Client` handles authorize-URL build, code→token exchange, refresh. User-approved "python library" for OAuth. | Exact pin, release ≥7 days old at install time (e.g. `Authlib==1.9.1`, released 2026-10-14; verify before pinning) |
| `cryptography` | Fernet symmetric encryption of stored tokens (arrives transitively via Authlib; pinned directly because we import it) | Same rule |

### Setup-time only

`@hubspot/cli` (`hs`) — registers the Outmate app with HubSpot. Node ≥20 is already a repo requirement. Not a runtime dependency.

### Explicitly NOT needed

- No Celery/Redis/worker queue — phase-1 sync is user-initiated button clicks.
- No new frontend dependencies — fetch + zod + existing components.
- No ngrok/public endpoint in phase 1 — only needed when webhooks arrive (Phase 2).

### "Could we do it manually?"

Two readings, two answers:

1. **Hand-rolled OAuth (no new lib):** technically possible — the code exchange is two POSTs over httpx. But you re-implement `state` lifecycle, refresh-on-expiry edge cases, and error normalization for ~zero savings. Authlib is the right call.
2. **Manual CSV export/import:** defeats the purpose — no remote-ID matching, no dedupe, no write-back, no freshness. Rejected.

---

## 3. Pipelines (arrow format)

### 3.1 OAuth connect

```
[Connect HubSpot] click in /integrations
  → GET /api/integrations/hubspot/connect           (FastAPI)
  → backend mints one-time `state` → oauth_states row (SQLite, 10-min TTL)
  → 302 redirect to HubSpot consent URL              (Authlib builds URL + scopes)
  → user selects "Outmate Sandbox", approves scopes
  → HubSpot 302 → GET /api/integrations/hubspot/callback?code&state
  → backend validates + consumes state (one-time; mismatch → 422 oauth_state_mismatch)
  → Authlib exchanges code → {access_token, refresh_token, expires_in ~30min}
  → tokens Fernet-encrypted → integration_connections row (provider='hubspot')
  → 302 → frontend /integrations?hubspot=connected
  → status card renders Connected + portal id
```

### 3.2 Import — HubSpot → Outmate

```
[Import from HubSpot] (sheet picker on /integrations)
  → POST /api/integrations/hubspot/import {sheet_id, company_ids?, contact_ids?}
  → ensure_fresh_token (auto-refresh if expired; refresh failure → not_connected)
  → GET /crm/v3/objects/companies (+ associations → contacts)
  → company `domain` → normalize_domain().compare_key
  → dedupe twice: (a) existing integration_links remote_id, (b) row compare_key
  → companies → SheetService.import_rows (existing: cap 100/call, sheet cap 200)
  → integration_links rows: {remote_id ↔ row_id, properties snapshot}
  → contacts → integration_links rows (row_id NULL — snapshot only, §4 contacts decision)
  → SheetRunner.resolve_pending_identities (existing background pass)
  → SSE row events → grid fills in
```

### 3.3 Enrich (existing engine — unchanged)

```
rows → Exa structured-output cell runs → Field{value, status, sources} per cell
```

### 3.4 Push — Outmate → HubSpot

```
User reviews done cells on linked rows → Push approval dialog
  → POST /api/integrations/hubspot/push {sheet_id, approvals:[{row_id, column_id}]}
  → backend re-reads cell values server-side (client sends WHICH cells, never WHAT values)
  → column name → outmate_<slug> property via integration_field_map
  → PATCH /crm/v3/objects/companies/{remote_id} (per linked row; contacts are pull-only in v1)
  → integration_push_log audit row per write (ok | error)
  → per-item result list returned → dialog shows pushed / failed
```

### 3.5 Manual refresh (pull CRM changes)

```
[Refresh from HubSpot]
  → POST /api/integrations/hubspot/refresh {sheet_id}
  → batch GET linked remote_ids
  → diff remote properties vs stored snapshot
  → return change report {row_id, property, old, new}
  → update integration_links snapshot ONLY — never silently overwrites cells
```

**Full loop:** `HubSpot → import → rows → enrich → review → approve → push → outmate_* properties updated on the same remote records (no duplicates, no sales-owned fields touched)`

---

## 4. HubSpot environment setup (manual, user-performed)

1. Free developer account → **Development → Testing → Test Accounts → Create**: name `Outmate Sandbox`, default Enterprise test tier.
2. `npm install -g @hubspot/cli@latest` → `hs account auth` → `hs project create` → App, restricted distribution, OAuth, skip UI features.
3. In `app-hsmeta.json` `config` object — redirect URL **must match the backend route exactly**:
   ```json
   "auth": {
     "type": "oauth",
     "redirectUrls": ["http://localhost:8000/api/integrations/hubspot/callback"],
     "requiredScopes": [
       "crm.objects.contacts.read",
       "crm.objects.contacts.write",
       "crm.objects.companies.read",
       "crm.objects.companies.write"
     ],
     "optionalScopes": [],
     "conditionallyRequiredScopes": []
   }
   ```
   *(Deviation from the brief's `/api/v1/...` path: the codebase's convention is `/api/` — e.g. `/api/sheets` — so integration routes live under `/api/integrations/hubspot/*`.)*
4. `hs project upload` → app Auth tab → copy **Client ID / Client Secret** into `backend/.env` (never `NEXT_PUBLIC_*`, never committed).
5. App Distribution settings → add `Outmate Sandbox` as test installation → install.
6. In the sandbox: seed 2–3 companies (with `domain` set) + a few contacts associated to them.
7. Create custom company properties manually (per decision — keeps 4 scopes only):
   - `outmate_tech_stack` — Single-line text
   - `outmate_description` — Multi-line text
   - `outmate_growth_signals` — Multi-line text
   - `outmate_leadership` — Multi-line text
   - `outmate_last_enriched` — Date picker *(optional)*

**Sandbox limits:** expires after 90 days without an API call; cannot sync to other accounts; not convertible to production. Documented here so demo expectations are set.

---

## 5. Backend design

### 5.1 New package `app/integrations/hubspot/`

Mirrors the existing `app/providers/` discipline — one module per concern, one retry boundary, secrets never logged.

| File | Responsibility |
|---|---|
| `oauth.py` | Authlib wrapper: `authorize_url(state)`, `exchange_code(code)`, `ensure_fresh_token(record)` — refreshes when `expires_at` is within a 60 s skew, persists rotated tokens via `store` |
| `client.py` | `HubSpotClient` protocol + `HttpHubSpotClient` (httpx, `https://api.hubapi.com`, Bearer): `list_objects`, `batch_read`, `patch_object`, `list_associations`. Single retry on 429 (honors `Retry-After` ≤ budget) / 5xx / timeout — same shape as `HttpExaProvider` |
| `store.py` | Repo functions for new tables — connection CRUD with encrypt/decrypt at the boundary, `oauth_states` create/consume (TTL + single-use), links upsert/list, field-map read, push-log append. One connection per unit of work, `BEGIN IMMEDIATE` writes — same as `app/sheets/repo.py` |
| `mapping.py` | Column name + `OutputType` → `outmate_<slug>` property name; rejects unmapped/standard properties; slug rules: lowercase, `[a-z0-9_]`, ≤50 chars |
| `service.py` | Business rules: import dedupe/link, approval filtering (only `done` cells on linked rows, only mapped columns), refresh diffing, per-item push results |
| `mock.py` | `FakeHubSpotClient` — in-memory portal for `HUBSPOT_MODE=mock`; every response discloses `mock: true` |

### 5.2 `app/errors/integration_error.py` + handler in `main.py`

`IntegrationError(code, message)` → mapped onto the shared `ErrorResponse` like `SheetError`:

| code | HTTP |
|---|---|
| `hubspot_not_configured` | 503 (missing client id/secret/token key — never a silent mock) |
| `hubspot_not_connected` | 409 |
| `oauth_state_mismatch` | 422 |
| `oauth_exchange_failed` | 502 |
| `hubspot_api_error` | 502 |
| `import_conflict` | 409 |

### 5.3 `app/api/integrations.py` — router

Thin layer over `service.py`; deps resolved via `app.state` like `SheetDeps` (`app.state.hubspot` = client or `None`, `app.state.integration_service`, `app.state.settings`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/integrations/hubspot/connect` | Mint `state` (`secrets.token_urlsafe(32)`, one-time, 10-min TTL) → 302 to HubSpot |
| GET | `/api/integrations/hubspot/callback` | Validate+consume state → exchange code → encrypt+store tokens → 302 to `HUBSPOT_POST_CONNECT_URL` (`?hubspot=connected` or `?hubspot_error=<code>`) |
| GET | `/api/integrations/hubspot/status` | `{connected, portal_id, scopes, connected_at, last_sync_at, companies_imported, contacts_imported, mock}` — tokens never leave this layer |
| DELETE | `/api/integrations/hubspot/connection` | Disconnect — deletes the connection row; links kept (reconnect resumes) |
| GET | `/api/integrations/hubspot/objects?object_type=companies\|contacts` | Preview list `{remote_id, name, domain, properties…}` for the import picker |
| POST | `/api/integrations/hubspot/import` | `{sheet_id, company_ids?, contact_ids?}` — empty/absent ids = import all fetched; → `{created, skipped, linked, rows}` |
| POST | `/api/integrations/hubspot/push` | `{sheet_id, approvals:[{row_id, column_id}]}` → `{results:[{row_id, property, status, error?}]}` |
| POST | `/api/integrations/hubspot/refresh` | `{sheet_id}` → `{changes:[{row_id, remote_id, property, remote_value, local_snapshot}]}` |

### 5.4 `app/config.py` additions

```python
hubspot_client_id: SecretStr | None = None
hubspot_client_secret: SecretStr | None = None
hubspot_redirect_uri: str = "http://localhost:8000/api/integrations/hubspot/callback"
hubspot_scopes: str = "crm.objects.companies.read crm.objects.companies.write crm.objects.contacts.read crm.objects.contacts.write"
hubspot_token_key: SecretStr | None = None          # Fernet key (urlsafe base64, 32B)
hubspot_mode: Literal["live", "mock", "off"] = "live"
hubspot_post_connect_url: str = "http://localhost:3000/integrations"
hubspot_max_qps: int = 5                             # token bucket, same pattern as exa_max_qps
```

Plus `hubspot_configured` property (all of client id + secret + token key non-blank → `True`). `backend/.env.example` documents every new var.

### 5.5 `app/sheets/db.py` — `SCHEMA_VERSION` 1 → 2

`init_db` gains a minimal migration branch: existing v1 databases apply the new `CREATE TABLE IF NOT EXISTS` statements and re-stamp `user_version = 2` (fresh DBs get the full DDL directly). Fallback documented: delete `sheets.db` — acceptable for a prototype, but the migration branch is small and honest.

New tables (all keyed by `provider` for Salesforce reuse):

```sql
CREATE TABLE IF NOT EXISTS integration_connections (
    provider TEXT PRIMARY KEY,              -- 'hubspot'
    portal_id TEXT,
    access_token_enc TEXT NOT NULL,         -- Fernet-encrypted
    refresh_token_enc TEXT NOT NULL,
    token_expires_at TEXT NOT NULL,
    scopes TEXT NOT NULL,
    mock INTEGER NOT NULL DEFAULT 0,
    connected_at TEXT NOT NULL,
    last_sync_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    expires_at TEXT NOT NULL,               -- now + 10 min; consumed = deleted
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_links (
    provider TEXT NOT NULL,
    object_type TEXT NOT NULL,              -- 'company' | 'contact'
    remote_id TEXT NOT NULL,
    sheet_id TEXT REFERENCES sheets(id) ON DELETE CASCADE,
    row_id TEXT REFERENCES "rows"(id) ON DELETE CASCADE,  -- NULL for contacts (snapshot-only)
    properties_json TEXT NOT NULL,          -- remote property snapshot for refresh diffs
    remote_updated_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (provider, object_type, remote_id)
);

CREATE TABLE IF NOT EXISTS integration_field_map (
    provider TEXT NOT NULL,
    object_type TEXT NOT NULL,
    column_name TEXT NOT NULL,              -- Outmate column name
    remote_property TEXT NOT NULL,          -- 'outmate_*' only (enforced in mapping.py)
    direction TEXT NOT NULL DEFAULT 'push', -- 'push' | 'pull' | 'both'
    owner TEXT NOT NULL DEFAULT 'outmate',  -- 'outmate' | 'hubspot'
    PRIMARY KEY (provider, object_type, column_name)
);

CREATE TABLE IF NOT EXISTS integration_push_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    row_id TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    property TEXT NOT NULL,
    value TEXT,
    status TEXT NOT NULL,                   -- 'ok' | 'error'
    error TEXT,
    created_at TEXT NOT NULL
);
```

### 5.6 `app/schemas/integration.py`

Pydantic request/response models in the existing style (`extra="forbid"`, `str_strip_whitespace`, `Field(min_length=…)`): `ImportRequest`, `ImportResponse`, `PushRequest`, `PushResult`, `PushResponse`, `RefreshRequest`, `RefreshChange`, `RefreshResponse`, `HubSpotStatus`, `RemoteObjectPreview`.

---

## 6. Field mapping & conflict policy

**Direction HubSpot → Outmate (import/pull):**

| HubSpot property | Outmate field | Rule |
|---|---|---|
| `name` | `input_name` | write-once at import |
| `domain` / `website` host | `input_domain` | write-once; normalized via `normalize_domain` |
| other company props | `integration_links.properties_json` | snapshot only — surfaces in refresh diffs, never overwrites cells |
| contact props (name/email/title + associated company) | `integration_links` snapshot | display-only in v1 |

**Direction Outmate → HubSpot (push):**

| Outmate source | HubSpot property | Rule |
|---|---|---|
| approved column value | `outmate_<slug>` | write only on explicit approval; only `done` cells with a non-null `Field.value`; `unavailable` cells are never pushed |
| anything else | standard properties | **never written** — ownership rule |

**Ownership:** HubSpot owns every standard property (name, domain, industry, reps' fields). Outmate owns the `outmate_*` namespace exclusively. Conflicts are therefore impossible by construction in v1; Phase 2 adds field-level direction/owner rules via `integration_field_map` for any `both`-direction mappings.

**Dedupe:** remote `companyId` → `integration_links` PK (a record already linked is skipped, not duplicated) AND `compare_key` domain dedupe via existing `import_rows` (a pasted row for the same domain stays single — link attaches to the existing row rather than creating a twin). Contacts: no rows — linked snapshots shown on the integrations page and the company row inspector.

---

## 7. Frontend design

| File | What |
|---|---|
| `app/integrations/page.tsx` | HubSpot card: connection status badge, portal id, scopes, companies/contacts imported (from `/status` — never hardcoded), last sync, buttons: Connect / Disconnect / Import / Push / Refresh. Reads `?hubspot=connected` / `?hubspot_error=` after the OAuth redirect. |
| `lib/api/integrations.ts` | Fetch client, same `SheetResult`-union discipline (never throws; `network` / `invalid_response` / `validation` / `api_error`) |
| `lib/schema/integration.ts` | zod mirrors of `app/schemas/integration.py` |
| `components/sheet/push-hubspot-dialog.tsx` | Approval UI: linked rows × mapped columns → done cells only, checkboxes, submit → `/push`, per-item result list |
| `app/page.tsx` | "Integrations" nav link in header |
| *(optional)* `components/sheet/row-profile-sheet.tsx` | Show linked contact snapshot on company rows |

State handling follows `page.tsx`'s `ListState` pattern (loading / ready / error with real `code`, `message`, `requestId`).

---

## 8. Testing strategy

**Automated — zero live HubSpot calls** (AGENTS.md hard rule):

- `tests/test_hubspot_client.py` — `respx`-mocked `api.hubapi.com`: list/batch/patch success, 401/403/429/5xx mapping to `IntegrationError` codes, single-retry behavior.
- `tests/test_hubspot_oauth.py` — `respx`-mocked `oauth.hubapi.com` token endpoint + `api.hubapi.com/oauth/v1/access-tokens/*`: state mint/consume/TTL-expiry, exchange failure → `oauth_exchange_failed`, refresh-on-expiry path with fake clock.
- `tests/test_integrations_store.py` — tmp-DB: Fernet roundtrip, single-use state, link upsert dedupe, push-log append, cascade on sheet delete.
- `tests/test_integrations_service.py` — in-memory `FakeHubSpotClient`: import dedupe (remote-id + compare_key), contacts → snapshot links, push approval filtering (only done+linked+mapped), refresh diff output.
- `tests/test_api_integrations.py` — TestClient + `dependency_overrides`: each endpoint's happy path + error-code mapping; `hubspot_not_configured` → 503.
- `tests/test_config.py` — new settings + `hubspot_configured`.
- Frontend vitest: schema mirror roundtrip, client union mapping, integrations page states (disconnected / connected / error).

**Manual checklist (demo, appended to doc):** connect sandbox → status Connected → import companies appear as rows + contacts as links → run enrichment columns → approve + push → verify `outmate_*` values on the sandbox records → refresh shows remote edits → disconnect → status Disconnected. Token refresh verified by forcing expiry (shorten `token_expires_at` in DB or wait 30 min).

**Verification commands** (from `backend/`): `ruff format --check .`, `ruff check .`, `mypy app`, `pytest`; (from `frontend/`): `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`. Plus the AGENTS.md secret scans.

---

## 9. Phases

| Phase | Contents |
|---|---|
| **1 — this doc's implementation scope** | OAuth connect/disconnect/status; import companies→rows + contacts→snapshots; remote-ID + domain dedupe; approved push to `outmate_*`; manual refresh (diff report); `HUBSPOT_MODE=mock`; full test coverage above |
| **2 — selective two-way sync** | `integration_field_map`-driven direction/owner rules beyond the namespace convention; incremental pull; HubSpot webhooks (public endpoint required — ngrok locally); conflict surface in UI |
| **3 — GTM workflows** | Scheduled enrichment, CRM-triggered research, qualified-prospect routing, stale-data monitoring; **Salesforce** slotting into `app/integrations/<provider>/` — tables are already `provider`-keyed |

---

## 10. Risks, deviations, and notes

- **Absent referenced docs:** `docs/spec.md`, `docs/tech.md`, `docs/implementation-plan.md`, `docs/plan.md`, `frontend/docs/api-contract.md` are not in this clone — this document is the feature contract; if those docs later appear, they take precedence and conflicts must be surfaced, not silently resolved.
- **AGENTS.md deviations (flagged, owner-visible):** two new dependencies (Authlib + cryptography — user-approved "python library"); `SCHEMA_VERSION` bump with a migration branch; OAuth tokens are third-party app credentials — the "no auth" rule concerns Outmate's own API and is unchanged.
- **No auth on the API:** consistent with the rest of the prototype — the connect/callback routes are public to localhost; a deployed build must sit behind a trusted boundary or add auth first.
- **Token-at-rest encryption** is Fernet with a key in `backend/.env` — honest at-rest protection for a local prototype, not a KMS substitute; documented as such.
- **Contact write-back** is deferred: contacts are pull-only snapshots in v1; `crm.objects.contacts.write` is already in scopes so Phase 2 needs no re-consent.
- **Rate limits:** HubSpot OAuth apps are limited (~100 req/10 s daily cap per portal burst) — `hubspot_max_qps` token bucket + the single-retry boundary covers the demo scale; batch reads (`/crm/v3/objects/{type}/batch/read`) used for refresh.
- **`docs/AGENTS.md` session protocol** still applies to the implementation session: read docs first, touch only allowed files, never weaken tests, run all verification.
