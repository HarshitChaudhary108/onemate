# Plan — Identity fallback: handle zero identity results more intelligently

**Date:** 2026-10-16
**Status:** awaiting approval — implementation not started
**Constraint:** no live provider calls. All implementation testing is fixture-based
(mock providers / scripted fakes; the test network guard blocks non-loopback
sockets). A live confirmation of the new path is a separate, explicitly approved
smoke test (~$0.007) and is not required to ship this change.

## 0. Context and scope

**Problem.** A live run for `orangeslice.ai` returned "Unresolved identity /
Company not confirmed" after a single zero-result identity search. Exa's `company`
category is a specialized index (~50M companies, updated weekly); a young company
may simply not be in it, so the domain-scoped company-category query can return
zero results even though the site has public pages. The run then stopped without a
second attempt.

**Scope.** Implement one bounded, broader fallback identity search that runs only
when the primary identity search finds no pages on the requested domain. Keep:

- the strict host-match identity gate (acceptance rules are never loosened);
- the MoltSets gate (MoltSets is never called without a resolved identity);
- the four-logical-searches budget;
- the retry policy, the post-identity concurrency cap (≤ 2), and the report schema.

**Out of scope.** Running overview/growth/leadership research while identity stays
unresolved (a larger change to status/schema/UI semantics); name-only fallback;
any schema change; any new dependency.

**Interpretation note.** "Separate the gate from the ability to research a
company" is implemented as: the identity phase gets one bounded broader attempt
within the existing budget before concluding — not as researching without an
identity.

## 1. Current behavior (verified in code)

- One identity attempt: `identity_query(...)` → `"<name or domain> company"`,
  `category="company"`, `includeDomains=[compare_key]`, 5 results —
  `backend/app/research/queries.py:47-64`.
- Strict resolution: ≥ 1 hit must host-match the domain (subdomain ok, `www.`
  stripped); zero matches → `resolved=false`, note `"no evidence for this domain"`
  — `resolve_identity` in `backend/app/research/orchestrator.py:155-167`.
- Unresolved → early return: all factual fields `unavailable`, MoltSets skipped
  (`"skipped: identity not resolved"`), 1 Exa call total —
  `backend/app/research/orchestrator.py:376-414`.
- Searches 2–4 run concurrently (cap 2) only after resolution —
  `backend/app/research/orchestrator.py:416-430`.

## 2. Binding-doc conflict — resolve before implementation (AGENTS.md rule)

The change contradicts the current docs, so per AGENTS.md ("never silently pick")
these amendments need owner approval first:

| File | Current text | Proposed amendment |
|---|---|---|
| `docs/implementation-plan.md` §5.4 (line 525) | `- No exploratory re-queries after an empty result.` | `- No exploratory re-queries after an empty result, except the single bounded identity fallback (search 1b); when it is used, the leadership search is skipped so the report still makes ≤ 4 logical searches.` |
| `docs/spec.md` §4 (line 97) | `A failed or empty lookup should not trigger repeated exploratory calls that burn the free allowance.` | Append: `When the identity search finds no pages on the requested domain, exactly one broader fallback identity search is allowed; it counts toward the four-search budget, and no other exploratory re-queries are made.` |
| `docs/spec.md` §3 decisions table (line 69) | "One focused company/identity search, … Target a maximum of four Exa searches per report…" | Add: "(plus at most one bounded fallback identity search when the domain yields no pages)" |
| `docs/tech.md` §6.3 (line 344) and §7 flow (line ~398) | 4-search plan | Add search "1b" and the conditional leadership skip to both places. |

Everything else in the spec is preserved: the host-match rule, "unresolved
identity prevents domain-dependent enrichment" (spec §12 DoD, line 490), and the
4-search cap.

## 3. Design (exact behavior)

### 3.1 Trigger

After the first `resolve_identity` call in `run_research`:

```
fallback runs ⇔ domain is not None
             ∧ not resolution.resolved
             ∧ resolution.note == "no evidence for this domain"
```

That literal is set in exactly one place (the zero-domain-hits branch), so the
trigger is exact. It deliberately does **not** fire for
`"supplied name and domain do not agree"` (the domain has pages; a broader search
cannot fix a name mismatch) and never for name-only inputs.

### 3.2 The fallback request

New pure builder in `backend/app/research/queries.py`:

```python
def identity_fallback_query(name: str | None, domain: NormalizedDomain) -> ExaSearchRequest:
    subject = name or domain.compare_key
    return ExaSearchRequest(
        query=f"{subject} company",
        num_results=NUM_RESULTS,          # 5
        include_domains=[domain.compare_key],
    )
```

Same subject, same domain scoping — the **only** change is dropping
`category="company"`. Without it, Exa searches all indexed pages on the domain,
not just company-index entries. `type` stays `"auto"` (adapter default), no date
filters. The parameter set is documented-supported (`category` is optional;
`includeDomains` is not restricted for the remaining filters — see
`docs/provider-notes.md`).

### 3.3 Validation — unchanged rules

The fallback result goes through the *same* `resolve_identity(...)`: ≥ 1 hit must
host-match the domain (subdomains ok, `www.` stripped); with a supplied name, ≥ 1
domain-matching hit must also name-match; the resolved name still comes from the
hit title (host-label fallback + note). No acceptance criterion is loosened — the
fallback only widens the search, never the gate.

### 3.4 Budget — stays ≤ 4 logical searches per report

| Path | Searches | Total |
|---|---|---|
| Primary resolves (today's normal path) | identity, overview, growth, leadership | 4 — unchanged |
| Fallback used, resolves | identity, **fallback**, overview, growth — **leadership skipped** | 4 |
| Fallback used, still empty | identity, fallback | 2 (report ends early) |

Leadership is the recommended casualty because `overview` carries 3 factual
fields + the folded objective, `growth` drives the hypothesis, and
decision-makers have a MoltSets backstop when enrichment is configured.
(Alternative: drop `growth` instead — open decision #2.) Retry policy unchanged
(≤ 1 adapter-level retry per request, outside the 4).

### 3.5 Outcome matrix

| Case | Result |
|---|---|
| Fallback resolves | Normal resolved report; `providers.exa.calls` = 4; leadership skipped; decision_makers from MoltSets only, else `unavailable` + listed in `missing_fields` |
| Fallback returns hits but none host-match (provider ignoring `includeDomains`) | Still unresolved — the gate holds; candidates preserved in the report |
| Fallback returns empty | Same unresolved report shape as today; note `"no evidence for this domain"`; `providers.exa.calls` = 2; MoltSets still skipped |
| Fallback raises `ProviderError` | `ResearchUnavailable` → HTTP 502 with the provider code — same rule as a failed primary identity search (a 200 "no evidence" would be misleading if the provider was down) |

### 3.6 Disclosure (no schema change)

- `providers.exa.note` (already rendered by the UI —
  `frontend/components/provider-status.tsx:40-45`) gets an honest note when the
  fallback ran. Resolved: `"Identity confirmed via a broader fallback search; the
  leadership search was skipped to stay within the four-search budget."`
  Still unresolved: `"The identity search and a broader fallback search both
  returned no pages on this domain."`
- `frontend/components/identity-notice.tsx` — update the explanation for
  `"no evidence for this domain"` to mention both searches. Keep the substring
  `"no public pages on this domain"` so
  `frontend/tests/report-states.test.tsx:109` keeps passing.
- `company_identity.note` semantics unchanged (`null` on success). No `Report`
  schema change → `backend/tests/fixtures/report.schema.json`, the frontend Zod
  mirror, and the API contract are untouched (verified by the schema snapshot
  test).

### 3.7 Out of scope / unchanged

Name-only inputs; the name/domain-mismatch branch; the MoltSets gate; retry and
backoff; the concurrency cap; no new dependencies.

## 4. File-by-file changes

**Backend**

1. `backend/app/research/queries.py` — add `identity_fallback_query`; update the
   module docstring (search list becomes 1 / 1b / 2 / 3 / 4-with-conditional-skip).
2. `backend/app/research/orchestrator.py` — after the first `resolve_identity`
   (line 374): trigger check → `await exa.search(identity_fallback_query(...))` →
   merge `exa_calls` / `exa_mock` / `exa_usage` (mirroring the existing pattern at
   lines 451–453) → `identity_result = fallback_result` (so `_identity_fields`
   sources/candidates come from the fallback evidence) → re-run `resolve_identity`.
   Change the gather (lines 425–430) to build a conditional stage list
   `[("overview", …), ("growth", …)]` `(+ [("leadership", …)] if not fallback_used)`
   and iterate that list in the result loop (lines 435–453). Set `exa_summary.note`
   in both the early-return (line ~378) and final (line ~522) paths. Update the
   module docstring (identity rules + fallback + budget).
3. `backend/app/providers/exa_mock.py` — new marker `fallback.test`: for it, the
   **primary** identity request (`category == "company"`) → `exa_empty.json`;
   every other request → `_intent_fixture(request)` (the fallback gets
   `exa_identity_hit.json`, later stages get their normal fixtures). Add the
   marker to the host-rewrite set (`_SUCCESS_MARKERS` stays as-is; add
   `_FALLBACK_MARKER` and use a combined tuple in `search()` and the docstrings).
   No new fixture file needed.

**Backend tests**

4. `backend/tests/test_queries.py` — `identity_fallback_query` tests (query text
   for name / domain-only, `category is None`, `include_domains == [key]`,
   5 results, no dates).
5. `backend/tests/test_orchestrator.py` — extend the `stage_key` helper (line 57)
   so a category-less request with `include_domains` classifies as
   `"identity_fallback"` (today it falls into `"identity"` via the
   `include_domains` branch), then add scripted tests:
   - fallback resolves → request sequence exactly
     `["identity", "identity_fallback", "overview", "growth"]`,
     `len(exa.requests) == 4`, resolved report, no leadership request;
   - fallback empty → unresolved, note unchanged, exactly 2 requests, MoltSets
     not called;
   - no fallback when primary resolves (extends
     `test_exa_search_count_at_most_four`);
   - no fallback for name-only input, and none for the name/domain-mismatch
     branch;
   - fallback `ProviderError` → `ResearchUnavailable` with the provider code;
   - **sabotage check**: fallback hits on *other* hosts must not resolve
     (validation intact).
6. `backend/tests/test_research_api.py` — one API-level test using the file's
   existing `deps_for` / `make_client` pattern: fallback scenario → 200,
   `company_identity.resolved == true`, `providers.exa.calls == 4`.
7. `backend/tests/test_report_fixtures.py` — add `test_fallback_snapshot`
   (request `domain="fallback.test"`, `request_id="req-fixture-0009"`, new
   snapshot `backend/tests/fixtures/reports/fallback.json`); regenerate
   `no_evidence.json` (its `providers.exa.calls` becomes 2); update
   `Utf8FixtureExa`'s rewrite-marker lookup (it imports `_SUCCESS_MARKERS` today)
   for the new marker.

**Frontend**

8. `frontend/components/identity-notice.tsx` — richer explanation text
   (substring-preserving); update `frontend/tests/report-states.test.tsx` only if
   the substring cannot be kept.

**Docs** (after approval of §2): `docs/spec.md`, `docs/tech.md`,
`docs/implementation-plan.md`, `README.md` (Identity resolution, Cost discipline
budget line, Mock vs live scenario table + `fallback.test` row, and a truthful
"not live-tested" bullet for the fallback path), `docs/provider-notes.md`
(document the fallback request variant as docs-verified, not live-tested).
`AGENTS.md` and `agent_session_prompts.md` unchanged.

**Implementation order:** §2 approvals → `queries.py` → `orchestrator.py` →
`exa_mock.py` → tests/fixtures/snapshots → frontend text → docs/README.

## 5. Test plan (fixtures first, no live calls)

- All new tests use scripted fakes / fixture adapters; `backend/tests/conftest.py`
  already deletes provider env vars per test and blocks non-loopback sockets
  (lines 61–82), so no real traffic is possible.
- New mock scenario `fallback.test` makes the whole path demonstrable in
  `EXA_MODE=mock` (primary identity search empty → fallback hit → resolved report
  with normal MoltSets fixture enrichment).
- Sabotage checks (tests that must fail if the guard is weakened):
  off-domain fallback hits do not resolve; fallback never runs when the primary
  resolves; the 4-search invariant is asserted in every fallback test.
- Snapshot regeneration is deliberate:
  `UPDATE_SNAPSHOTS=1 pytest tests/test_report_fixtures.py`.

## 6. Verification commands

```powershell
cd backend
ruff format --check .
ruff check .
mypy app
pytest
# only when intentionally updating fixtures:
#   UPDATE_SNAPSHOTS=1 pytest tests/test_report_fixtures.py
```

```powershell
cd frontend        # if the UI text changes
npm run lint
npm run typecheck
npm test
npm run build
```

Optional local mock demo (no keys, no provider calls):
`EXA_MODE=mock MOLTSETS_MODE=mock` → submit `fallback.test` (resolved report) and
`no-evidence.test` (unresolved, 2 Exa calls).

## 7. Risks and safeguards

- Fallback returns low-quality pages that still host-match → statuses stay
  conservative (evidence rules untouched); with name + domain input the
  name-match requirement still applies; worst case the identity resolves to the
  domain owner, which is what a domain-scoped search implies.
- Extra cost only in the failure path: +1 Exa search (~$0.007) when triggered,
  0 otherwise.
- Fixture-based tests cannot validate Exa's real index behavior — flagged in
  README / provider-notes as not live-tested until an approved smoke test.

## 8. Approval checklist (open decisions)

1. **Approve the doc amendments in §2** (required — they are the binding-doc
   conflict).
2. **Budget trade-off:** skip `leadership` on fallback runs (recommended) — or a
   different allocation?
3. **Note wording** in §3.6 — as drafted, or adjust?
