# P0 notes — Exa OpenAPI spec deviations + U1–U4 (documentation-only)

Fetched `https://exa.ai/docs/exa-spec.yaml` on 2026-10-17 (OpenAPI 3.1.0, "Exa Public
API" v2.0.0). **No live provider calls were made** — owner amendment. Every U-answer below
is marked *unverified against the live API*.

## Deviations from the plan's assumptions

1. **`required` is a documented outputSchema keyword.** The plan says "documented schema
   controls are type, description and properties." The spec's `OutputSchemaObject` schema
   is `{type: "object", description, properties, required: string[]}` plus open
   `additionalProperties: JsonValue`. So `required` is supported. `enum` is not explicitly
   modeled, but the open `additionalProperties` means extra keywords may pass validation —
   whether Exa *honors* them is unknown. The plan's conservative rule stands: send only
   `type`, `description`, `properties`; validate enums server-side.

2. **New outputSchema limits not in the plan.** Object schemas are limited to **10 total
   properties** (nested and array-item properties count) and **2 levels of nesting**; every
   array must define `items`. Root type must be `"text"` or `"object"`. Our
   `{found, value}` schema is well inside these limits; P4's planner must respect them if
   schemas ever grow.

3. **`people` + LinkedIn-only `includeDomains` is NOT in the spec.** The plan says people
   "only accepts LinkedIn domains in includeDomains." The spec documents only: `company`
   and `people` reject `startPublishedDate`/`endPublishedDate`, and `people` also rejects
   `excludeDomains` — unsupported filters return 400. Our conservative builder already
   sends no `includeDomains` for people, so this stays safe; the LinkedIn claim is
   undocumented.

4. **`costDollars` is an object, not a scalar.** Shape:
   `{total, search: {neural, keyword}, summary, contents: {text, highlights, summary}}` —
   all optional floats, described as *estimated* cost, "not an invoice record." Spend
   accounting (P3 budget) must read `costDollars.total`. The existing adapter already
   preserves the dict verbatim, so nothing breaks. The spec's `x-payment-info` for
   `/search` says dynamic pricing, min $0.007 / max $0.015 per request.

5. **New response fields:** `warnings[]` (e.g. `IGNORED_FIELD` for deprecated/no-op
   fields — "log them; do not pass them to a model") and `notices[]` ("pass them to your
   model with the results"). Both omitted when empty. Tolerant parsing should accept and
   ignore them; neither needs handling for the MVP.

6. **Deprecated response fields:** `resolvedSearchType` ("may be an empty string; do not
   branch on it") and `context` (use `results[].highlights`/`text` instead). We use
   neither — no action.

7. **`searchTime` may exclude output-synthesis time** — it can be lower than end-to-end
   latency. Don't treat it as full request latency when outputSchema is set.

8. **New search types exist:** `instant`, `fast`, `deep-lite` (consistent ~4 s), plus the
   planned `auto`, `deep`, and excluded `deep-reasoning`. The plan uses only `auto`/`deep`
   — no change needed. `additionalQueries` works only with deep-search types (unused).

9. **`stream: true`** enables SSE for synthesized output when outputSchema is set — unused
   by the plan (our SSE is server-side over our own events).

## U-answers (documentation-only — all *unverified against the live API*)

- **U1 — does outputSchema change cost?** *Unverified live.* Spec documents dynamic
  `/search` pricing $0.007–$0.015 and ~2 s added synthesis latency; `costDollars.summary`
  suggests synthesis is itemized separately, so a surcharge is plausible but unmeasured.
  Working rule: estimates come from `EXA_PRICING.md` documented rates; actual spend is
  recorded only from provider-returned `costDollars.total`, so no code depends on a
  guessed price.

- **U2 — `grounding[].field` strings for `{found, value}`:** *Unverified live.* Spec:
  `field` is a "field path in output.content, e.g. `content` or `companies[0].funding`."
  For our schema expect `"value"` (possibly `"found"`). Acceptance rule per plan: field
  `== "value"` or `startswith("value")`; otherwise the cell has no grounding →
  `unavailable`. Never guess.

- **U3 — are `enum`/`required` accepted?** *Partially resolved by spec, unverified live.*
  `required` is a documented `OutputSchemaObject` keyword (array of strings). `enum` is
  not modeled; open `additionalProperties` means it may pass spec validation but its
  effect is unknown. We send only `type`/`description`/`properties` and validate enums
  server-side — correct under every outcome.

- **U4 — Groq model access + strict JSON Schema shape:** *Unverified live.* No call made.
  Model comes from `PLANNER_MODEL` (default `openai/gpt-oss-120b`); a missing
  `GROQ_API_KEY` returns 503, never a silent mock. Strict-mode rules (every property in
  `required`, `additionalProperties: false` on every object, no `tools`/`tool_choice` in
  the same request) are taken from Groq docs as recorded in plan §1.2.

- **U5, U6 — open.** Resolved by automated gates: U5 by the P6 `npm run build`, U6 by the
  P5 SSE integration test.
