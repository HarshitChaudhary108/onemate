import type { Report } from "@/lib/schema/report";
import { SourceList } from "@/components/source-list";
import { StatusBadge } from "@/components/status-badge";
import { formatDate, humanizeToken } from "@/lib/utils/format";

const EMPTY_STATE = "No supported signals found in this run.";

/**
 * Growth signals: one dated item per supporting hit, typed, with its own
 * sources. The empty state uses the backend's own note (which is exactly
 * "No supported signals found in this run.") or the same fixed text.
 */
export function GrowthSignals({ field }: { field: Report["growth_signals"] }) {
  const items = field.value ?? [];

  return (
    <section
      aria-labelledby="growth-heading"
      data-testid="growth-signals"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="growth-heading" className="text-lg font-semibold text-ink-strong">
          Growth signals
        </h2>
        <StatusBadge status={field.status} sources={field.sources} />
      </div>

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-ink-muted">{field.note ?? EMPTY_STATE}</p>
      ) : (
        <ul className="mt-4 space-y-4">
          {items.map((signal, index) => (
            <li
              key={`${signal.type}-${signal.date ?? "undated"}-${index}`}
              data-testid="growth-item"
              className="border-l-2 border-accent-line pl-4"
            >
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-full border border-accent-line bg-accent-soft px-2 py-0.5 font-medium text-accent-strong">
                  {humanizeToken(signal.type)}
                </span>
                {formatDate(signal.date) ? (
                  <span className="text-ink-muted">{formatDate(signal.date)}</span>
                ) : null}
              </div>
              <p className="mt-1 text-sm leading-relaxed text-ink">{signal.summary}</p>
              <SourceList sources={signal.sources} className="mt-2" />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
