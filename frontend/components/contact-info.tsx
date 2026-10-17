import { Mail } from "lucide-react";

import type { Report } from "@/lib/schema/report";
import { SourceList } from "@/components/source-list";
import { StatusBadge } from "@/components/status-badge";
import { formatDate } from "@/lib/utils/format";

/**
 * Contact information: provider-returned business contact details only.
 * Emails are rendered as plain text (never as links), the risk grade is shown
 * with its fixed caveat, and absent values are hidden.
 */
export function ContactInfo({ field }: { field: Report["contact_information"] }) {
  const items = field.value ?? [];
  if (items.length === 0) return null;

  return (
    <section
      aria-labelledby="contact-heading"
      data-testid="contact-information"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="contact-heading" className="text-lg font-semibold text-ink-strong">
          Contact information
        </h2>
        <StatusBadge status={field.status} sources={field.sources} />
      </div>
      <p className="mt-2 text-xs text-ink-muted">
        Email risk grade is a deliverability signal, not identity proof.
      </p>
      <ul className="mt-4 space-y-4">
        {items.map((item, index) => (
          <li
            key={`${item.person_name}-${index}`}
            data-testid="contact-item"
            className="border-l-2 border-accent-line pl-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-medium text-ink-strong">{item.person_name}</p>
              {item.mock ? (
                <span className="rounded-full border border-inferred-line bg-inferred-bg px-1.5 py-0.5 text-[0.65rem] font-semibold text-inferred-ink">
                  Sample
                </span>
              ) : null}
            </div>
            <dl className="mt-1 space-y-0.5 text-sm">
              {item.business_email ? (
                <div className="flex flex-wrap items-center gap-2">
                  <dt className="text-xs font-mono uppercase tracking-wide text-ink-muted">Business email</dt>
                  <dd className="inline-flex items-center gap-1 text-ink">
                    <Mail aria-hidden="true" className="h-3.5 w-3.5 text-ink-muted" />
                    {item.business_email}
                  </dd>
                </div>
              ) : null}
              {item.email_risk_grade ? (
                <div className="flex flex-wrap items-center gap-2">
                  <dt className="text-xs font-mono uppercase tracking-wide text-ink-muted">Risk grade</dt>
                  <dd className="text-ink">{item.email_risk_grade}</dd>
                </div>
              ) : null}
              {formatDate(item.email_last_validated) ? (
                <div className="flex flex-wrap items-center gap-2">
                  <dt className="text-xs font-mono uppercase tracking-wide text-ink-muted">Last validated</dt>
                  <dd className="text-ink">{formatDate(item.email_last_validated)}</dd>
                </div>
              ) : null}
            </dl>
            <SourceList sources={item.sources} className="mt-2" />
          </li>
        ))}
      </ul>
    </section>
  );
}
