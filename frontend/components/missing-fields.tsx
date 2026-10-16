import type { Report } from "@/lib/schema/report";
import { humanizeField } from "@/lib/utils/format";

/**
 * Missing factual fields for this run (the backend's own `missing_fields`).
 * An empty list is reported as such — it is a result, not an absent value.
 */
export function MissingFields({ report }: { report: Report }) {
  const missing = report.missing_fields;

  return (
    <section
      aria-labelledby="missing-heading"
      data-testid="missing-fields"
      className="rounded-card border border-line bg-surface p-5 shadow-card"
    >
      <h2 id="missing-heading" className="text-lg font-semibold text-ink-strong">
        Missing fields
      </h2>
      {missing.length === 0 ? (
        <p className="mt-2 text-sm text-ink-muted">
          Every factual field has a value in this run.
        </p>
      ) : (
        <>
          <p className="mt-2 text-sm text-ink-muted">
            These factual fields could not be supported by evidence in this run:
          </p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {missing.map((name) => (
              <li
                key={name}
                className="rounded-full border border-unavailable-line bg-unavailable-bg px-2.5 py-1 text-xs font-medium text-unavailable-ink"
              >
                {humanizeField(name)}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
