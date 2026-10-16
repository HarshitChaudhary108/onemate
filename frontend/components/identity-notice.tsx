import { ScanSearch } from "lucide-react";

import type { Report } from "@/lib/schema/report";
import { SourceList } from "@/components/source-list";

/**
 * Unresolved / no-evidence state. Explains what was searched (from the echoed
 * request and the backend's own identity note) and that enrichment was
 * skipped. It never phrases the lack of results as proof that the company
 * lacks signals.
 */

function describeSearch(request: Report["request"]): string {
  const { company_name: name, domain } = request;
  if (name && domain) return `“${name}” (${domain})`;
  if (name) return `“${name}”`;
  if (domain) return `“${domain}”`;
  return "the submitted input";
}

function noteExplanation(note: string | null | undefined): string {
  switch (note) {
    case "no evidence for this domain":
      return "Two identity searches (company-focused and a broader domain search) returned no public pages on this domain.";
    case "no public-source hits matched the supplied company name":
      return "The identity search returned no public pages that matched this company name.";
    case "top name-matching hits point to more than one host":
      return "More than one company matched this name, so no single company could be confirmed.";
    case "supplied name and domain do not agree":
      return "The supplied name and domain did not agree on one company.";
    default:
      return "The identity search could not confirm a single company for this input.";
  }
}

export function IdentityNotice({ report }: { report: Report }) {
  const identity = report.company_identity;
  const candidates = [
    ...(identity.company_name.sources ?? []),
    ...(identity.domain.sources ?? []),
  ];
  const enrichmentNote = report.providers.moltsets.note;

  return (
    <section
      aria-labelledby="identity-heading"
      data-testid="identity-notice"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <h2
        id="identity-heading"
        className="flex items-center gap-2 text-lg font-semibold text-ink-strong"
      >
        <ScanSearch aria-hidden="true" className="h-5 w-5 text-ink-muted" />
        What was searched
      </h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink">
        <p>
          Public sources were searched for {describeSearch(report.request)}
          {report.request.research_objective ? ", using the supplied research objective" : ""}.
          No company identity was confirmed, so the enrichment steps (company profile and people
          lookups) were skipped and no further provider calls were made.
        </p>
        <p>{noteExplanation(identity.note)}</p>
        {identity.note ? (
          <p className="text-xs text-ink-muted">Provider note: “{identity.note}”</p>
        ) : null}
        {enrichmentNote ? (
          <p className="text-xs text-ink-muted">Enrichment: {enrichmentNote}</p>
        ) : null}
        <p className="rounded-card border border-unavailable-line bg-unavailable-bg p-3 text-sm text-unavailable-ink">
          An unresolved identity means this run could not confirm which company to research. It is
          not evidence that the company has no growth signals, decision-makers, or public
          information.
        </p>
      </div>
      <SourceList sources={candidates} label="Candidate sources considered" className="mt-4" />
    </section>
  );
}
