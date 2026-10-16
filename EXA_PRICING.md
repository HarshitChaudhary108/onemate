# Exa Pricing & Usage Guide

> **What this is:** a plain-language summary of how Exa charges for API usage,
> compiled from Exa's official documentation on **2026-10-16**. It is a cost
> guide for this prototype, not a bill and not official Exa material.
> Prices and plan rules can change — always check the official pages
> (links at the bottom).

---

## TL;DR

- Exa is **pay-as-you-go**: every API call deducts from a **prepaid credit
  balance** (1 credit = $1). No subscription, no minimum spend.
- New teams get **free credits**: $10 the day you sign up, plus a one-time
  $10 onboarding bonus; the free balance **resets to $10 on the 1st of every
  month**. No payment method is required.
- **No payment method = no surprise charge.** When credits run out, requests
  are **blocked** with HTTP `402` — they are not silently billed.
- You spend real money only if you **add credits** or enable **auto-recharge**.
- This prototype: **mock mode = $0.00**; a **live report ≈ $0.03** (measured:
  4 searches × $0.007).

---

## 1. How billing works

| Plan | How you pay | Free credits | Rate limit |
|---|---|---|---|
| **Free** | Nothing required | $10 on signup + $10 one-time onboarding bonus, resets to $10 monthly | 10 QPS |
| **Pay as you go** | Prepaid credits (card via Stripe) | — | 10 QPS (25 QPS for 90 days after buying $1,000 in any 30-day window) |
| **Enterprise** | Custom volume pricing, optional postpaid invoices | — | Custom |

Free-tier rules (all of them):

- $10 is available **in full the day you sign up**, regardless of the date.
- The balance then **resets to $10 on the first day of every month**.
- Free credits **do not accumulate** — unused credits do not roll over, and the
  grant never holds more than $10 at once.
- Only a user's **first team** receives the monthly grant and the one-time bonus.
- The one-time $10 bonus **does not expire**.
- Free credits are **never refunded or paid out**.
- The Free tier **does not include Exa Snapshot**.

---

## 2. Usage units (glossary)

| Unit | Meaning | How it is billed |
|---|---|---|
| **Request** | One API call | Per 1,000 requests, e.g. $7/1k = $0.007 per call |
| **Result** | One search result returned | First 10 are included in the base price; each result above 10 costs $1/1k |
| **Page / content type** | One URL, one view of it (`text`, `highlights`, or `summary`) | On `/contents`: $1/1k pages, **each content type billed separately** |
| **AI page summary** | An Exa-generated summary (extra LLM call) | $1/1k pages, on any endpoint that returns one |
| **Credit** | Prepaid dollar balance | Usage draws it down at the rates below |

---

## 3. Full price list (every product)

| Product | Rate | Used by this prototype? |
|---|---|---|
| `/search` — `instant` | $4 / 1k requests ($0.004 each) | No |
| `/search` — `fast`, `auto` | $7 / 1k requests ($0.007 each) | **Yes** (`type: "auto"`) |
| Extra results (above 10 per request) | $1 / 1k results | No (we request 5) |
| AI page summaries | $1 / 1k pages | No (never requested) |
| `/contents` | $1 / 1k pages **per content type** | No |
| Deep Search — `deep-lite` / `deep` | $12 / 1k requests | No |
| Deep Search — `deep-reasoning` | $15 / 1k requests | No |
| `/answer` | $5 / 1k requests | No |
| `/monitors` | $15 / 1k requests | No |
| `/agent` — fixed efforts | $0.012 (minimal) → $1.00 (xhigh) per run | No |
| `/agent` — metered | $0.10 / ACU, $0.005 / search call, $0.02 / email, $0.07 / phone | No |

> **Note:** this app only ever calls `/search` with `type: "auto"`. Everything
> else in the table is listed so you can see what other Exa products would cost
> if the code were changed to use them.

---

## 4. What this prototype actually spends

**Settings used:** `type: "auto"`, `numResults: 5` (≤ 10, so no extra-result
charge), `contents: {"highlights": true}`. No full text, no AI summaries.

**Measured live on 2026-10-16** (real calls from this repo):

| Measurement | Result |
|---|---|
| One search (real call) | **$0.007** — usage returned `{"total": 0.007, "search": {"neural": 0.007}}`; no separate contents charge appeared |
| One full report | **4 searches ≈ $0.028** |
| Free credits ≈ | **~1,400 searches** of the type this app uses ($10 ÷ $0.007) |

**Per-report budget (hard-coded in the pipeline):** ≤ 4 searches + at most
1 retry each (retries only on timeout, network error, 5xx, or rate limit with
a usable retry time ≤ 10 s).

---

## 5. All usage scenarios (what you pay)

| Scenario | Exa calls | Cost |
|---|---|---|
| Mock mode (`EXA_MODE=mock`) | 0 | **$0.00** |
| Automated tests | 0 (network blocked) | **$0.00** |
| Live: identity only (unresolved / no evidence) | 1 | ≈ $0.007 |
| Live: full report | up to 4 | ≈ $0.028 |
| Live: full report + one retry | up to 5 | ≈ $0.035 |
| Live: identity search fails (HTTP 502) | 1–2 | ≈ $0.007–0.014 |
| Rate limited (429) | +1 retry, only if reset time ≤ 10 s | ≈ +$0.007 |
| Credits exhausted | 0 (blocked with 402) | $0.00 |

---

## 6. When the credits run out (every possibility)

| Situation | What happens |
|---|---|
| Balance reaches $0, no auto-recharge | API returns `402 Payment Required` (`NO_MORE_CREDITS`). Requests are **blocked** until you add credits. No card on file → no charge. |
| An API key hits its spending budget | `402` (`API_KEY_BUDGET_EXCEEDED`) — lower the key's limit or adjust the budget in the dashboard. |
| Team spending budget reached | `402` (`TEAM_BUDGET_EXCEEDED`). |
| Auto-recharge enabled | When the balance hits your chosen threshold, Exa **automatically buys credits** (real money, Stripe). Recharge amount: $5–$10,000. Optional monthly cap (leave blank or $0 = no cap). |
| In this app | `402` becomes a report error `exa_credits_exhausted` (non-retryable) — the app stops, tells you clearly, and never keeps retrying. |

---

## 7. Rate limits (not cost, but related)

| Endpoint | Limit |
|---|---|
| `/search`, `/answer` | 10 queries per second (QPS) |
| `/search` with deep types | 5 QPS |
| `/contents` | 100 QPS |
| Over the limit | `429 Too Many Requests` + `Retry-After` header |

This app retries a `429` **once**, and only when the required wait is ≤ 10 s —
so a rate limit can add at most one extra paid call.

---

## 8. How to check your balance and manage money

- **Dashboard → Billing:** <https://dashboard.exa.ai/billing> — add credits,
  set auto-recharge, view invoices.
- Receipts are emailed from `billing@exa.ai` for purchases and auto recharges.
- The search API **does not return your balance** — only per-call cost metadata
  (`costDollars`) when the provider includes it.
- Per-key historical usage is available through Exa's Team Management API if
  ever needed.

---

## 9. Cost rules for this repository

- **Free credits only.** Never add a payment method, buy credits, enable
  auto-recharge, or upgrade a plan for this prototype without approval.
- Automated tests **never** call providers (a network guard blocks them).
- Live calls are **manual and approved**, one deliberate test at a time.
- **Mock mode is free** and always available for demos:
  `EXA_MODE=mock MOLTSETS_MODE=mock uvicorn app.main:app --port 8000`.
- No paid or out-of-scope endpoints (phone lookup, personal email, etc.) are
  ever called.

---

## 10. Caveats and unknowns

- Rates and plan rules can change; this summary reflects the official docs as
  of **2026-10-16**.
- Whether a **failed** request (4xx other than 402) is charged is not documented
  — avoid spamming failures.
- Highlights requested inside `/search` showed **no separate charge** in our
  measured calls; the "$1 / 1k pages per content type" rate applies to the
  `/contents` endpoint.
- The account's actual usage may vary with request options (result count,
  summaries, search type).

---

## Sources (accessed 2026-10-16)

- Pricing: <https://exa.ai/docs/reference/pricing>
- Billing and rate limits: <https://exa.ai/docs/reference/billing>
- Error codes: <https://exa.ai/docs/admin/error-codes>
