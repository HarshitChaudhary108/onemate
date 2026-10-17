/**
 * Display helpers for the sheets grid (P7). Same rule as utils/format.ts:
 * nothing is invented — helpers format provider/backend data or the caller
 * hides the value.
 */

import type { Status } from "@/lib/schema/report";
import type { CellStatus, CellValue, IdentityStatus, OutputType } from "@/lib/schema/sheet";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 6,
});

/** The rendered cell value for one output type. `null` means "no value to
 *  show" — the caller renders the status word instead, never a guess. */
export function formatCellValue(
  value: CellValue | null | undefined,
  outputType: OutputType,
): string | null {
  if (value === null || value === undefined) return null;
  switch (outputType) {
    case "number":
      return typeof value === "number" ? NUMBER_FORMAT.format(value) : String(value);
    case "boolean":
      return value === true ? "Yes" : value === false ? "No" : String(value);
    case "text":
    case "enum":
    case "url":
    case "date":
      return String(value);
  }
}

/** Provider-reported dollars. Amounts under a cent keep four decimals so a
 *  real $0.007 spend is never rounded into silence. */
export function formatUsd(amount: number): string {
  if (amount !== 0 && Math.abs(amount) < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

/** Screen-reader label for a cell's execution state — the icon + text rule
 *  means every state also has words. */
export function cellStateLabel(status: CellStatus): string {
  switch (status) {
    case "idle":
      return "Not run";
    case "queued":
      return "Queued";
    case "running":
      return "Running";
    case "done":
      return "Done";
    case "error":
      return "Error";
    case "blocked":
      return "Blocked";
  }
}

/** Field-status word used next to its icon inside a done cell. Reuses the
 *  StatusBadge vocabulary, shortened for a 36px row. */
export function statusMarkLabel(status: Status): string {
  switch (status) {
    case "verified":
      return "Verified";
    case "provider-reported":
      return "Reported";
    case "inferred":
      return "Inferred";
    case "unavailable":
      return "Unavailable";
  }
}

/** Identity badge on the pinned company column — one label per
 *  identity_status, each paired with an icon by the caller. */
export function identityLabel(status: IdentityStatus): string {
  switch (status) {
    case "pending":
      return "Pending";
    case "resolved":
      return "Resolved";
    case "unresolved":
      return "Unresolved";
    case "ambiguous":
      return "Ambiguous";
    case "error":
      return "Identity error";
  }
}

/** Parse one pasted import line into {name?, domain?}. A line is either
 *  `name<tab>domain`, `name,domain`, or a single token classified like the
 *  research form (bare host or URL → domain, otherwise name). */
export function parseImportLine(line: string): { name?: string; domain?: string } | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  const cut = trimmed.search(/[\t,]/);
  if (cut >= 0) {
    const name = trimmed.slice(0, cut).trim();
    const domain = trimmed
      .slice(cut + 1)
      .trim()
      .replace(/^https?:\/\//, "")
      .replace(/\/.*$/, "");
    const out: { name?: string; domain?: string } = {};
    if (name) out.name = name;
    if (domain) out.domain = domain;
    return out.name || out.domain ? out : null;
  }
  const token = trimmed.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  if (/^[^\s]+\.[a-z]{2,}$/i.test(token)) return { domain: token };
  return { name: trimmed };
}
