# Provider notes — M2 documentation check

Date accessed: **2026-10-16**. Method: read-only fetch of public documentation
pages and the published OpenAPI schemas. At the time of writing, **no provider API
had been called**. A single approved live Exa identity search was later run on
2026-10-16 (recorded at the end of this document); everything else below is
docs-verified, not live-verified, and **no MoltSets request has ever been made**.

## Exa

URLs consulted:

- <https://docs.exa.ai/reference/search> (renders the OpenAPI spec inline)
- <https://exa.ai/docs/exa-spec.yaml> (OpenAPI source of truth)
- <https://exa.ai/docs/llms.txt> (index page; states both auth schemes)
- <https://exa.ai/docs/search/best-practices> (uses `Authorization: Bearer`)

VERIFIED facts:

- Endpoint: `POST https://api.exa.ai/search` (`servers: https://api.exa.ai`,
  path `/search`).
- Auth: the OpenAPI spec declares **two** security schemes for this endpoint —
  `x-api-key` header (`apiKey` scheme) **and** `Authorization: Bearer`
  (`bearer` scheme). The reference page's own curl sample uses `x-api-key`.
  Either is accepted; this codebase standardizes on `x-api-key` everywhere.
- Request body fields (schema `SearchRequest`): `query` (string, required),
  `type` (one of `instant`, `fast`, `auto`, `deep-lite`, `deep`,
  `deep-reasoning`; `auto` is the documented default), `numResults`
  (integer 1–100, default 10), `category` (enum: `company`, `publication`,
  `news`, `personal site`, `financial report`, `people`; other strings are
  accepted as hints), `includeDomains` (array of hosts/paths/wildcards, max
  1200), `excludeDomains`, `startPublishedDate` / `endPublishedDate` (ISO 8601
  date-time strings).
- Highlights are requested via `contents: {"highlights": true}` (schema
  `ContentsOptions`). `contents.text` returns full page text and
  `contents.summary` returns generated summaries — **neither is requested**
  by this adapter.
- Filter restrictions (VERIFIED, quoted from the `category` description):
  "`company` and `people` do not support `startPublishedDate` or
  `endPublishedDate`, and `people` also does not support `excludeDomains`.
  Requests that set an unsupported filter return a 400 error."
  `includeDomains` is **not** listed as unsupported for `company` or `people`,
  so the identity search may combine `category: "company"` with
  `includeDomains: [domain]`.
- Response (`SearchResultsResponse`, one of the two `SearchResponse` arms):
  `results` (required array), `requestId`, `searchTime` (ms), `costDollars`
  (`total`, `search.neural`/`keyword`, `contents.text`/`highlights`/`summary`),
  `warnings`, `notices`. Each result requires `title` and `url`; optional:
  `id`, `publishedDate` (documented as `YYYY-MM-DD`), `author`, `highlights`
  (string array), `highlightScores` (float array), `image`, `favicon`, `text`,
  `summary`, `subpages`, `entities`, `extras`.
- The current result schema has **no `score` field** — only `highlightScores`.
  `ExaHit.score` stays optional and will normally be `None`.
- Documented error statuses on `/search`: 400, 401, 402, 403, 429, 500, 503.
  Error body schema `ErrorResponse`: `{requestId, error, tag}` (`error` is a
  human-readable string, `tag` an open-ended machine tag).

ASSUMPTIONS (not confirmed by docs):

- `Retry-After` is assumed to arrive as an HTTP header carrying a delta in
  seconds. HTTP-date form is not handled; an unparsable value is treated as
  absent.
- `costDollars` is taken verbatim as `usage` metadata when present; individual
  breakdown keys are not required.
- `RETRY_WAIT_BUDGET_SECONDS = 10.0` is an **implementation choice** (not a
  provider limit): if `Retry-After` exceeds 10 s the adapter does not wait and
  raises `exa_rate_limited` after the first attempt, so the UI is never left
  hanging. Retries without `Retry-After` use a fixed 1.0 s bounded backoff
  (under the 2 s cap in the plan).
- HTTP statuses outside the plan's error table (e.g. 403, 404, other 4xx, or a
  non-200 2xx) are mapped to `exa_bad_request`, non-retryable.
- `exa_invalid_response` (non-retryable) is an **additional internal code**
  for a 200 whose body cannot be parsed; it is not a documented Exa code.

## MoltSets

URLs consulted:

- <https://developer.moltsets.com/getting-started/overview>
- <https://developer.moltsets.com/getting-started/authentication>
- <https://developer.moltsets.com/api-reference/search/search-for-companies>
- <https://developer.moltsets.com/api-reference/search/search-for-people>
- <https://developer.moltsets.com/llms.txt> (index)

VERIFIED facts:

- Base URL: `https://api.moltsets.com/api/v1/tools/`; each tool is a `POST`
  to its own path. This adapter uses only `search_companies` and
  `search_people` (both in the "Search" tool category).
- Auth: `Authorization: Bearer <key>` on every request. Every request must
  also carry a **non-empty `User-Agent`**; missing `User-Agent` is rejected
  with 403 `forbidden` before authentication is checked.
- `search_companies` request body: `query` (company-name free text), `domain`
  (exact domain lookup; accepts a string or list; URLs are normalized
  server-side), plus `industry`, `employee_range`, `revenue_range`,
  `naics_code`, `country`, `state`, `city`, and `exclude_*` filters. The
  adapter sends only `{"domain": <compare_key>}`.
- `search_people` request body: `query`, `title`, `company`, `company_domain`,
  `country`, `state`, `city`, `seniority` (enum: `Intern`, `Entry`, `Senior`,
  `Manager`, `Director`, `VP`, `Head`, `C Suite`, `Owner`, `Partner` — note
  the space in `"C Suite"`), `industry`, `department`, `functional_area`,
  `naics_code`, `linkedin_industry`, `employee_range`, `revenue_range`,
  `exclude_*` filters, `limit` (integer, default 10, max 25), `offset`
  (default 0). The adapter sends `company_domain`, `seniority`, and
  `limit` (hard-capped at 5 in code).
- Envelope on a completed call: HTTP 200 with
  `{ "results": { "results": [...], "total": N }, "status": "ok",
  "metadata": {...} }`. A miss is HTTP 200 with `status: "not_found"` and an
  empty `results.results` — **not an error**. The only 404 is
  `tool_not_found`.
- `metadata`: `tokens_used`, `tokens_remaining` (token-bound accounts only),
  `fair_use` (`records_remaining_5h`, `records_reset_5h`,
  `records_remaining_1w`, `records_reset_1w`), and on 429 responses
  `retry_after` (seconds, **inside the body**, not a header).
- Error body shape: `{ "error": { "code": ..., "message": ... },
  "metadata": {...}? }`. Branch on `error.code`, not message text.
- Documented error codes: `invalid_input` (422), `unauthorized` (401),
  `forbidden` (403 — missing `User-Agent`), `account_inactive` (403),
  `plan_restricted` (403), `insufficient_tokens` (402),
  `insufficient_phone_tokens` (402), `rate_limited` (429 burst guard),
  `fair_use_limit_exceeded` (429 record window — or the free-tier one-time
  search trial, which has **no** `retry_after`),
  `fair_use_requests_exceeded` (429 request window, has `retry_after`),
  `tool_not_found` (404), `internal_error` (500).
- Company result fields: `name`, `domain`, `industry`, `linkedin_url`,
  `revenue_range`, `employee_count` (string), `employee_range`, `city`,
  `state`, `country`, `naics_codes`, `naics_titles`, `linkedin_industry`,
  `logo_url`, `_id`, `_score`.
- People result fields: `full_name`, `first_name`, `last_name`, `title`,
  `headline`, `seniority`, `city`, `state`, `country`, `current_industry`,
  `functional_area`, `linkedin_url`, `business_email`,
  `business_email_risk_score` (letter grade — a **deliverability** signal,
  not identity proof), `business_email_validated_at`,
  `current_role_start_date`, `last_updated_at`, nested `company`, `_id`,
  `_score`.
- Plans doc: free tier ($0) = 100 lifetime search records, **5 per call max**
  — consistent with this adapter's `limit <= 5` cap.

ASSUMPTIONS (not confirmed by docs):

- `metadata.retry_after` (body) is treated as the primary retry hint; the
  HTTP `Retry-After` header is also read as a fallback. A `429
  fair_use_limit_exceeded` **with** a usable retry time is treated as
  retryable (docs say "wait `retry_after` seconds"); only without a retry
  time does it become `moltsets_quota_exhausted`.
- The same `RETRY_WAIT_BUDGET_SECONDS = 10.0` budget rule applies (an
  implementation choice): a longer required wait means no retry.
- `fair_use_requests_exceeded` is mapped to `moltsets_rate_limited`
  (retryable), `insufficient_phone_tokens` to `moltsets_payment_required`
  (unreachable — phone tools are never called).
- A 403 whose body code is absent/unrecognized maps to
  `moltsets_plan_restricted` per the plan's disambiguation rule.
- `moltsets_invalid_response` (non-retryable) is an additional internal code
  for unparseable 200 bodies.
- Whether a 200 with `status: "ok"` but an empty `results.results` array can
  occur is unspecified; the adapter treats it as `found=False`.

Everything above is **docs-verified** (and fixture-tested where the adapters
implement it). The one live Exa smoke search recorded below is the only live
provider call made from this repository; **no live MoltSets call has been made**.
Before the first live MoltSets call, re-check these pages and the account's
allowed-tool schema.

## Live smoke test record

**2026-10-16 — Exa identity search (approved; exactly one call).** Run through the
backend adapter (`HttpExaProvider`, `x-api-key` header) with `EXA_MODE=live`:

- Query: `"Stripe company"`, `type: "auto"`, `numResults: 5`, `category: "company"`,
  `includeDomains: ["stripe.com"]`, highlights requested.
- Result: HTTP 200, `calls: 1` (no retry), 5 hits in ~1.8 s. Top hit
  `https://stripe.com/` ("Stripe"), 1 highlight, no published date (optional field
  absent → `None`).
- Usage returned by Exa: `{"total": 0.007, "search": {"neural": 0.007}}` — ≈ $0.007 of
  free credit consumed; no `contents` line was returned for this call.
- Confirmed live: the `x-api-key` auth scheme, the documented result shape parses,
  usage metadata arrives as a cost object, and optional fields stay optional.
- Not confirmed live: Exa error codes, retry metadata parsing, the other three query
  types, and every MoltSets behavior (no live MoltSets call has been made).
