/**
 * Display helpers for report rendering.
 *
 * Two rules encoded here:
 * - External links are re-checked on the client (`new URL`, http/https only)
 *   before they are ever rendered as an anchor.
 * - Nothing is invented: every helper either formats provider/backend data or
 *   hides it (returns `null`).
 */

import type { Report, Source } from "@/lib/schema/report";

/** The seven factual fields counted by the coverage summary (backend order). */
export const FACTUAL_FIELD_NAMES = [
  "description",
  "industry",
  "target_market",
  "employee_size",
  "growth_signals",
  "decision_makers",
  "contact_information",
] as const;

/** Accept only http(s) URLs; everything else is not renderable as a link. */
export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (!url.hostname) return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** Hostname of a link, for the visible link text. `null` when unusable. */
export function linkDomain(value: string | null | undefined): string | null {
  const safe = safeExternalUrl(value);
  if (!safe) return null;
  try {
    return new URL(safe).hostname;
  } catch {
    return null;
  }
}

const DATE_FORMAT = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const DATE_TIME_FORMAT = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
  timeZoneName: "short",
});

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** `2026-09-01` -> `Sep 1, 2026`. Unparseable values are hidden (`null`). */
export function formatDate(value: string | null | undefined): string | null {
  const date = parseDate(value);
  return date ? DATE_FORMAT.format(date) : null;
}

/** ISO datetime -> `Oct 16, 2026, 12:00 PM UTC`. */
export function formatDateTime(value: string | null | undefined): string | null {
  const date = parseDate(value);
  return date ? DATE_TIME_FORMAT.format(date) : null;
}

/** `growth_signals` -> `Growth signals`; `target_market` -> `Target market`. */
export function humanizeField(name: string): string {
  const spaced = name.replace(/_/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : name;
}

/** `leadership_change` -> `Leadership change`; `not_configured` -> `Not configured`. */
export function humanizeToken(value: string): string {
  return humanizeField(value);
}

/** A source is inspectable when it carries a safe URL. */
export function hasInspectableSource(sources: readonly Source[] | undefined): boolean {
  return (sources ?? []).some((source) => safeExternalUrl(source.url) !== null);
}

/** `Exa`, `MoltSets`, or `Exa + MoltSets`; `null` when no source is present. */
export function providerLabel(sources: readonly Source[] | undefined): string | null {
  const providers = [...new Set((sources ?? []).map((source) => source.provider))];
  if (providers.length === 0) return null;
  return providers.join(" + ");
}

/** The seven factual `Field` wrappers, in backend order. */
function factualFields(report: Report) {
  return [
    report.description,
    report.industry,
    report.target_market,
    report.employee_size,
    report.growth_signals,
    report.decision_makers,
    report.contact_information,
  ];
}

/** Every source attached anywhere in the report (mirrors the backend). */
export function collectSources(report: Report): Source[] {
  const sources: Source[] = [
    ...(report.company_identity.company_name.sources ?? []),
    ...(report.company_identity.domain.sources ?? []),
  ];
  for (const field of factualFields(report)) {
    sources.push(...(field.sources ?? []));
    const value = field.value;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item.sources) sources.push(...item.sources);
      }
    }
  }
  const hypothesis = report.potential_gtm_opportunity.value;
  if (hypothesis) {
    for (const point of hypothesis.rationale) sources.push(...(point.sources ?? []));
  }
  return sources;
}

/** True when mock data contributed anywhere (banner condition). */
export function isMockReport(report: Report): boolean {
  return report.data_mode !== "live" || collectSources(report).some((source) => source.mock);
}

/** Deduplicate sources by URL + provider, for display only. */
export function dedupeSources(sources: readonly Source[]): Source[] {
  const seen = new Set<string>();
  const result: Source[] = [];
  for (const source of sources) {
    const key = `${source.provider}|${safeExternalUrl(source.url) ?? "no-url"}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(source);
  }
  return result;
}

function fieldHasValue(value: unknown): boolean {
  if (value == null) return false;
  return !Array.isArray(value) || value.length > 0;
}

/** Factual fields with a value, out of the seven counted fields. */
export function coverageSummary(report: Report): { present: number; total: number } {
  const present = factualFields(report).filter((field) => fieldHasValue(field.value)).length;
  return { present, total: FACTUAL_FIELD_NAMES.length };
}

/** Human-readable data-mode label for the header. */
export function dataModeLabel(report: Report): string {
  switch (report.data_mode) {
    case "live":
      return "Live provider data";
    case "mock":
      return "Mock data";
    case "mixed":
      return "Mixed live + mock data";
  }
}

/**
 * Plain recovery hint derived from a stable backend error code
 * (plan M2 error tables). Unknown codes get no hint.
 */
export function recoveryHint(code: string): string | null {
  if (code.endsWith("_rate_limited")) return "Rate limited by the provider. Try again shortly.";
  if (code.endsWith("_quota_exhausted") || code === "exa_credits_exhausted") {
    return "The provider quota is exhausted. Try again later.";
  }
  if (code.endsWith("_timeout") || code.endsWith("_network")) {
    return "The provider did not respond in time. Try again shortly.";
  }
  if (code.endsWith("_server_error")) {
    return "The provider had a temporary server error. Try again shortly.";
  }
  if (code.endsWith("_invalid_key")) return "Check the provider API key in backend/.env.";
  if (code === "exa_not_configured") {
    return "Add EXA_API_KEY to backend/.env and restart the backend.";
  }
  if (code === "moltsets_missing_user_agent") {
    return "Set MOLTSETS_USER_AGENT on the backend and restart it.";
  }
  if (code === "moltsets_plan_restricted") {
    return "The provider plan does not allow this call; no retry will help.";
  }
  if (code === "moltsets_tool_not_found") {
    return "The provider endpoint was not found; the backend adapter may need updating.";
  }
  if (code === "exa_invalid_request" || code === "moltsets_invalid_input" || code === "exa_bad_request") {
    return "The provider rejected the request shape; this is a backend configuration issue.";
  }
  return null;
}

export type UsageEntry = { key: string; value: string };

/** Flatten a provider usage object into displayable key/value rows. */
export function usageEntries(usage: Record<string, unknown> | null | undefined): UsageEntry[] {
  if (!usage) return [];
  return Object.entries(usage).map(([key, value]) => ({
    key,
    value:
      value !== null && typeof value === "object" ? JSON.stringify(value) : String(value),
  }));
}
