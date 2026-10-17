import type { Report } from "@/lib/schema/report";
import { SourceList } from "@/components/source-list";
import { StatusBadge } from "@/components/status-badge";

const OVERVIEW_FIELDS = [
  ["description", "Description"],
  ["industry", "Industry"],
  ["target_market", "Target market"],
  ["employee_size", "Employee size"],
] as const;

/**
 * Overview: description, industry, target market, and employee size — each
 * with its own status badge and inline sources. Absent values are hidden; the
 * whole section disappears when nothing has a value.
 */
export function OverviewSection({ report }: { report: Report }) {
  const rows = OVERVIEW_FIELDS.map(([key, label]) => ({ key, label, field: report[key] })).filter(
    (row) => row.field.value != null,
  );
  if (rows.length === 0) return null;

  return (
    <section
      aria-labelledby="overview-heading"
      data-testid="overview-section"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <h2 id="overview-heading" className="text-lg font-semibold text-ink-strong">
        Overview
      </h2>
      <dl className="mt-4 space-y-5">
        {rows.map((row) => (
          <div key={row.key} data-testid={`overview-${row.key}`}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <dt className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">
                {row.label}
              </dt>
              <StatusBadge status={row.field.status} sources={row.field.sources} />
            </div>
            <dd className="mt-1 text-sm leading-relaxed text-ink">{row.field.value}</dd>
            <SourceList sources={row.field.sources} className="mt-2" />
          </div>
        ))}
      </dl>
    </section>
  );
}
