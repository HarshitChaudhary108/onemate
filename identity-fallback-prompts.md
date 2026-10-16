# Identity Fallback — Coding Agent Session Prompts

**Companion to:** `identity-fallback-plan.md` (the design; source of truth for behavior details)
**Project root:** `D:\onemate` (Windows)
**Use:** one prompt per fresh coding-agent session, in order: Prompt 1 → Prompt 2 → Prompt 3.

**Preconditions — check before running Prompt 1:**

- The project owner has approved the three decisions in `identity-fallback-plan.md` §8.
  These prompts implement the recommended choices:
  1. doc amendments exactly as written in §2;
  2. the leadership search is skipped when the fallback runs;
  3. disclosure wording as drafted in §3.6.
- If any decision changes, update `identity-fallback-plan.md` (and this file) first —
  never let the prompts and the plan disagree.

**Hard rules that apply to every prompt** (each prompt repeats them; listed here for the operator):

- No live provider calls — no `api.exa.ai`, no MoltSets endpoints. Tests use fixtures/fakes only.
- No real API keys; no new dependencies; no report-schema changes.
- Never weaken, delete, or skip a test to make it pass.
- Touch only the files each prompt lists; stop and ask otherwise.
- Every session ends with the final report format from `AGENTS.md`.

**File map (which prompt touches what):**

| Prompt | Touches |
|---|---|
| 1 | `docs/spec.md`, `docs/tech.md`, `docs/implementation-plan.md` |
| 2 | `backend/app/research/queries.py`, `backend/app/research/orchestrator.py`, `backend/app/providers/exa_mock.py`, `backend/tests/test_queries.py`, `backend/tests/test_orchestrator.py`, `backend/tests/test_research_api.py`, `backend/tests/test_report_fixtures.py`, `backend/tests/fixtures/reports/fallback.json` (new), `backend/tests/fixtures/reports/no_evidence.json` (regenerated) |
| 3 | `frontend/components/identity-notice.tsx`, `frontend/tests/report-states.test.tsx` (only if needed), `README.md`, `docs/provider-notes.md`, `identity-fallback-plan.md` (Status line) |

## How to run each session

1. Open a fresh agent session in `D:\onemate` (new session or `/clear`).
2. Paste only that prompt's text block (from the `text` code block below).
3. Wait for the agent's final report (AGENTS.md format).
4. Run the "Your verification" commands yourself. Never trust "all tests pass" without seeing output.
5. Commit only if everything passes — one commit per prompt, so you can roll back cleanly.
6. If something fails, fix it in the same session or a new one. Do not move to the next prompt.

---

## Prompt 1 — Policy amendments (docs only)

*Applies the approved amendments from `identity-fallback-plan.md` §2. No code in this session.*

```text
You are working in D:\onemate (Windows). This session implements exactly one step
of the approved plan identity-fallback-plan.md: the policy/document amendments
(section 2). Do NOT write or modify any code in this session.

Read before editing:
- AGENTS.md (session protocol, hard rules, final report format)
- identity-fallback-plan.md (the whole file; this session = section 2 only)
- docs/spec.md, docs/tech.md, docs/implementation-plan.md (the passages named below)

Approved decisions (identity-fallback-plan.md section 8):
- Amendments approved exactly as written in section 2.
- Budget trade-off: when the fallback search is used, the leadership search is
  skipped so a report still makes at most four logical Exa searches.
- Disclosure wording: as drafted in section 3.6.

Tasks (touch ONLY these three files):

1. docs/spec.md
   a) Section 3 decisions table, "Exa searches" row (around line 69): append to
      the strategy cell: "(plus at most one bounded fallback identity search
      when the requested domain yields no pages)".
   b) Section 4, the cost-discipline bullet around line 97 ("A failed or empty
      lookup should not trigger repeated exploratory calls that burn the free
      allowance."): append exactly:
      "When the identity search finds no pages on the requested domain, exactly
      one broader fallback identity search is allowed; it counts toward the
      four-search budget, and no other exploratory re-queries are made."

2. docs/tech.md
   a) Section 6.3 "Exa search plan and budget" (around line 344): add search
      "1b" — a broader fallback identity search (same subject and
      includeDomains as search 1; category omitted) that runs only when search
      1 finds no pages on the requested domain, and counts toward the
      four-search budget.
   b) Section 7 flow, the unresolved-identity branch (around line 398): state
      that the identity phase may make one bounded fallback search before
      concluding unresolved, and that when it is used the leadership search is
      skipped.

3. docs/implementation-plan.md
   a) Section 5.4, line 525: replace the bullet
      "- No exploratory re-queries after an empty result."
      with
      "- No exploratory re-queries after an empty result, except the single
      bounded identity fallback (search 1b); when it is used, the leadership
      search is skipped so the report still makes ≤ 4 logical searches."
   b) M3 task 1 (around line 320, "There are four logical searches at most"):
      add a "1b" entry for the fallback query builder (identity_fallback_query)
      and note the conditional leadership skip.

Constraints:
- Where identity-fallback-plan.md section 2 specifies exact text, use it
  verbatim (including the "≤ 4 logical searches" wording).
- Do not touch README.md, docs/provider-notes.md, any code file, or any test.
- No live provider calls; no new dependencies.
- If any wording conflicts with the plan or with AGENTS.md, STOP and ask.

Your verification:
- git status --short   (only the three docs files modified)
- git diff             (read the entire diff; confirm only the approved text
                        changed and nothing else)

Commit only if everything passes:
git add -A; git commit -m "docs: approve identity fallback policy amendments"

Finish with the final report format in AGENTS.md.
```

---

## Prompt 2 — Backend implementation, fixtures, and tests

*Implements `identity-fallback-plan.md` §3–§5 in the backend. Frontend and README are Prompt 3.*

```text
You are working in D:\onemate (Windows). This session implements the backend
decision logic, mock adapter, fixtures, and tests for the approved plan
identity-fallback-plan.md. Frontend files and README/provider-notes are NOT part
of this session.

Read before editing:
- AGENTS.md (session protocol, hard rules, final report format)
- identity-fallback-plan.md — sections 1, 3, 4, 5, 6
- backend/app/research/queries.py
- backend/app/research/orchestrator.py
- backend/app/providers/exa_mock.py
- backend/tests/conftest.py, backend/tests/test_queries.py,
  backend/tests/test_orchestrator.py, backend/tests/test_research_api.py,
  backend/tests/test_report_fixtures.py

Approved decisions (identity-fallback-plan.md section 8): the leadership search
is skipped when the fallback runs; note wording exactly as in section 3.6.

Tasks (touch ONLY the files listed):

1. backend/app/research/queries.py
   - Add identity_fallback_query(name: str | None, domain: NormalizedDomain)
     -> ExaSearchRequest: query f"{name or domain.compare_key} company",
     num_results=NUM_RESULTS, include_domains=[domain.compare_key], no category,
     no date filters (exact sketch in plan section 3.2).
   - Update the module docstring search list (1 / 1b / 2 / 3 / 4-with-skip).

2. backend/app/research/orchestrator.py
   - After the first resolve_identity call (around line 374):
     * trigger (plan section 3.1): domain is not None and not
       resolution.resolved and
       resolution.note == "no evidence for this domain";
     * await exa.search(identity_fallback_query(request.company_name, domain));
       on ProviderError raise ResearchUnavailable(code=exc.code,
       message=exc.message) exactly like the primary identity failure
       (around lines 361-367);
     * merge like the existing post-identity pattern (lines 451-453):
       exa_calls += fallback_result.calls;
       exa_mock = exa_mock or fallback_result.mock;
       exa_usage = fallback_result.usage if fallback_result.usage is not None
       else exa_usage;
     * identity_result = fallback_result; then re-run
       resolve_identity(request.company_name, domain, fallback_result, clock);
     * track fallback_used: bool.
   - Searches 2-4 (gather around lines 425-430): build the stage list
     conditionally — overview and growth always; leadership only when
     fallback_used is False. Keep asyncio.Semaphore(MAX_SEARCH_CONCURRENCY) and
     the per-stage failure isolation; iterate the built list in the result loop
     (around lines 435-453).
   - providers.exa.note when fallback_used (both the early-return summary
     around line 378 and the final summary around line 522):
     * resolved: "Identity confirmed via a broader fallback search; the
       leadership search was skipped to stay within the four-search budget."
     * still unresolved: "The identity search and a broader fallback search
       both returned no pages on this domain."
   - Update the module docstring (it is declared binding): identity rules + the
     fallback paragraph + the budget note.

3. backend/app/providers/exa_mock.py
   - Add marker "fallback.test". For requests whose text contains it:
     category == "company" -> exa_empty.json (the primary identity search finds
     nothing); every other request -> _intent_fixture(request).
   - Add the marker to the host-rewrite set: add _FALLBACK_MARKER; use a
     combined tuple (with _SUCCESS_MARKERS) for the rewrite lookup in search().
     Update the module and scenario_fixture docstrings. No new fixture file.

4. backend/tests/test_queries.py
   - Add tests for identity_fallback_query: name subject and domain-only
     subject query text; category is None; include_domains == [compare_key];
     num_results == 5; no date filters.

5. backend/tests/test_orchestrator.py
   - Extend stage_key (around line 57): a request with include_domains but no
     category classifies as "identity_fallback". Keep existing classifications
     unchanged (company category -> identity; name-only -> identity).
   - Add scripted tests using the existing dispatch/FakeExaProvider pattern:
     a) fallback resolves: identity -> empty; identity_fallback -> domain hit.
        Assert the request order exactly
        ["identity", "identity_fallback", "overview", "growth"],
        len(exa.requests) == 4, report resolved, no leadership request, and
        decision_makers has no values with a default (no-people) MoltSets fake.
     b) fallback empty: identity and identity_fallback -> empty. Assert
        unresolved, note "no evidence for this domain", exactly 2 requests,
        MoltSets never called.
     c) no fallback when the primary resolves: keep
        test_exa_search_count_at_most_four green; assert no "identity_fallback"
        request appears.
     d) no fallback for name-only input (assert exactly 1 request).
     e) no fallback for the name/domain-mismatch branch (note "supplied name
        and domain do not agree"; assert no fallback request).
     f) fallback ProviderError -> pytest.raises(ResearchUnavailable) with the
        provider code.
     g) sabotage check: fallback hits on other hosts must NOT resolve (the
        host gate stays strict).
   - Every fallback test must also assert the <= 4-search invariant.

6. backend/tests/test_research_api.py
   - One API-level test using the file's existing deps_for/make_client pattern:
     fallback scenario -> HTTP 200, company_identity.resolved true,
     providers.exa.calls == 4.

7. backend/tests/test_report_fixtures.py
   - Add test_fallback_snapshot: ResearchRequest(domain="fallback.test") with
     Utf8FixtureExa(mock=False) + Utf8FixtureMoltSets(mock=False), exa_mode and
     moltsets_mode "live", request_id "req-fixture-0009", snapshot file
     backend/tests/fixtures/reports/fallback.json. Assert resolved true and
     providers.exa.calls == 4 in the parsed snapshot.
   - Regenerate backend/tests/fixtures/reports/no_evidence.json (its
     providers.exa.calls becomes 2; the note stays "no evidence for this
     domain").
   - Update Utf8FixtureExa's rewrite-marker lookup for the new marker (it
     imports _SUCCESS_MARKERS today).
   - Regenerate snapshots ONLY with:
     UPDATE_SNAPSHOTS=1 pytest tests/test_report_fixtures.py
     then review the JSON diffs (git diff) before finishing.

Hard constraints:
- NO live provider calls (api.exa.ai and MoltSets endpoints are off-limits);
  all tests use fixtures/fakes; the conftest network guard must stay intact.
- No new dependencies. No schema changes: backend/app/schemas/report.py and
  backend/tests/fixtures/report.schema.json must stay byte-identical.
- Never weaken, delete, or skip a test to make it pass.
- Total logical Exa searches per report must stay <= 4 in every path.
- Do not touch frontend files, README.md, docs/provider-notes.md, or the docs
  amended in Prompt 1.
- If anything is ambiguous or contradicts the plan/code, STOP and ask.

Your verification (run all; report exact results and test counts):
cd backend
ruff format --check .
ruff check .
mypy app
pytest

Commit only if everything passes:
git add -A; git commit -m "feat: bounded identity fallback search (search 1b)"

Finish with the final report format in AGENTS.md.
```

---

## Prompt 3 — Frontend copy and documentation finalization

*Implements `identity-fallback-plan.md` §3.6, the frontend/docs items of §4, and marks the plan done.*

```text
You are working in D:\onemate (Windows). This session implements the frontend
copy and the documentation finalization for the approved plan
identity-fallback-plan.md. The backend implementation (Prompt 2) is complete and
verified. Do NOT change backend code in this session.

Read before editing:
- AGENTS.md
- identity-fallback-plan.md — sections 3.6, 4 (Frontend + Docs), 6
- frontend/components/identity-notice.tsx
- frontend/components/provider-status.tsx (context only: it already renders
  providers.exa.note — no change needed there)
- frontend/tests/report-states.test.tsx
- README.md — the sections: Identity resolution; Cost discipline and free-tier
  limits; Mock vs live mode; What has not been live-tested
- docs/provider-notes.md — the Exa section

Tasks (touch ONLY the files listed):

1. frontend/components/identity-notice.tsx
   - In noteExplanation, update the "no evidence for this domain" case to
     mention both searches, for example:
     "Two identity searches (company-focused and a broader domain search)
     returned no public pages on this domain."
   - Keep the substring "no public pages on this domain" (a test asserts it).

2. frontend/tests/report-states.test.tsx
   - Update only if the substring above is not kept; otherwise confirm the
     suite passes unchanged.

3. README.md
   a) Identity resolution section: document the bounded fallback — when the
      identity search finds no pages on the requested domain, one broader
      fallback search runs (same subject and domain scoping, no company
      category); if it resolves, the leadership search is skipped so the
      report still makes at most four Exa searches; if it does not, the run
      stays unresolved with the same honest note and MoltSets is never called.
   b) Cost discipline and free-tier limits: update the per-report budget line
      to include the fallback allocation (on fallback runs: identity +
      fallback + overview + growth = 4; leadership skipped).
   c) Mock vs live mode scenario table: add a fallback.test row — the primary
      identity search finds nothing; the broader fallback resolves the domain;
      full mock report.
   d) What has not been live-tested: add a bullet stating the identity-fallback
      path is fixture-tested only and has not been exercised against live Exa.

4. docs/provider-notes.md
   - In the Exa section, add the fallback request variant: same subject and
     includeDomains as the identity search, category omitted (category is
     optional; includeDomains is not restricted for the remaining filters).
     Mark it docs-verified, not live-tested.

5. identity-fallback-plan.md
   - Update the Status line:
     "awaiting approval — implementation not started"
     -> "implemented — fixture-tested (2026-10-16)".

Constraints:
- No live provider calls; no new dependencies; no backend changes.
- Keep the README's verification vocabulary exact (fixture-tested / locally
  tested / live-tested); never claim live testing for the fallback.
- If anything contradicts the plan or the implemented behavior, STOP and ask.

Your verification (run all; report exact results):
cd frontend
npm run lint
npm run typecheck
npm test
npm run build

Commit only if everything passes:
git add -A; git commit -m "docs+ui: identity fallback copy, README, provider notes"

Finish with the final report format in AGENTS.md.
```

---

## After all three prompts

- Full re-check on both apps: backend `ruff format --check .` / `ruff check .` /
  `mypy app` / `pytest` and frontend `npm run lint` / `npm run typecheck` /
  `npm test` / `npm run build` — all green.
- Optional local demo (no keys, no provider calls): `EXA_MODE=mock
  MOLTSETS_MODE=mock` → submit `fallback.test` (resolved report) and
  `no-evidence.test` (unresolved, 2 Exa calls).
- Optional live confirmation — **requires explicit owner approval** (~$0.007,
  one Exa search): run one live request for a domain whose company-category
  search returns nothing (e.g. `orangeslice.ai`). Until that actually runs, the
  fallback path stays "fixture-tested" in README/provider-notes — never claim
  otherwise.
