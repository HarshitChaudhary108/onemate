import { Lightbulb } from "lucide-react";

import type { Report } from "@/lib/schema/report";
import { SourceList } from "@/components/source-list";
import { StatusBadge } from "@/components/status-badge";

/**
 * GTM opportunity: always labelled "Inferred hypothesis", with rationale
 * points that carry their own sources and the backend's fixed caveat. When no
 * hypothesis could be built, the backend's explanatory note is shown instead.
 */
export function OpportunityHypothesis({
  field,
}: {
  field: Report["potential_gtm_opportunity"];
}) {
  const hypothesis = field.value;

  return (
    <section
      aria-labelledby="hypothesis-heading"
      data-testid="opportunity-hypothesis"
      className="rounded-card border border-inferred-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="hypothesis-heading" className="text-lg font-semibold text-ink-strong">
          GTM opportunity
        </h2>
        <StatusBadge status={field.status} sources={field.sources} />
      </div>

      {hypothesis ? (
        <div className="mt-3">
          <p className="inline-flex items-center gap-2 rounded-full border border-inferred-line bg-inferred-bg px-2.5 py-1 text-xs font-semibold text-inferred-ink">
            <Lightbulb aria-hidden="true" className="h-3.5 w-3.5" />
            Inferred hypothesis
          </p>
          <p className="mt-3 text-sm leading-relaxed text-ink">{hypothesis.summary}</p>
          {hypothesis.rationale.length > 0 ? (
            <div className="mt-4">
              <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                Rationale
              </h3>
              <ul className="mt-2 space-y-3">
                {hypothesis.rationale.map((point, index) => (
                  <li
                    key={`${point.point}-${index}`}
                    data-testid="hypothesis-point"
                    className="border-l-2 border-accent-line pl-4"
                  >
                    <p className="text-sm text-ink">{point.point}</p>
                    <SourceList sources={point.sources} className="mt-1.5" />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="mt-4 text-xs text-ink-muted">Caveat: {hypothesis.caveat}</p>
        </div>
      ) : (
        <p className="mt-3 text-sm text-ink-muted">
          {field.note ?? "No hypothesis could be formed from the evidence in this run."}
        </p>
      )}
    </section>
  );
}
