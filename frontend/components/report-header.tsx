import type { Report } from "@/lib/schema/report";
import {
  collectSources,
  coverageSummary,
  dataModeLabel,
  formatDateTime,
  safeExternalUrl,
} from "@/lib/utils/format";
import { StatusBadge } from "@/components/status-badge";

/**
 * Report header: company identity + canonical domain + overall status,
 * coverage summary (fields with a value / 7 factual fields), data mode, and
 * the run metadata (generated at, request id). Missing identity values are
 * hidden; for an unresolved run the submitted request is echoed and labelled.
 */
export function ReportHeader({ report }: { report: Report }) {
  const { company_identity: identity, request } = report;
  const name = identity.company_name.value;
  const domain = identity.domain.value;
  const coverage = coverageSummary(report);
  const generated = formatDateTime(report.generated_at);
  const requestedDomain = request.domain ? safeExternalUrl(`https://${request.domain}`) : null;
  const requestedDomainHost = requestedDomain ? new URL(requestedDomain).hostname : null;

  return (
    <header
      data-testid="report-header"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">
            {identity.resolved ? "Company" : "Unresolved identity"}
          </p>
          <h2 className="mt-1 text-2xl font-semibold tracking-tight text-ink-strong">
            {name ?? request.company_name ?? "Company not confirmed"}
          </h2>
          {domain ? (
            <p className="mt-1 text-sm text-ink-muted">
              Canonical domain: <span className="font-medium text-ink">{domain}</span>
            </p>
          ) : requestedDomainHost ? (
            <p className="mt-1 text-sm text-ink-muted">
              Requested domain: <span className="font-medium text-ink">{requestedDomainHost}</span>
            </p>
          ) : null}
          {request.research_objective ? (
            <p className="mt-2 text-sm text-ink-muted">
              Objective: <span className="text-ink">{request.research_objective}</span>
            </p>
          ) : null}
        </div>
        <StatusBadge
          status={report.overall_status}
          sources={collectSources(report)}
          className="text-sm"
        />
      </div>

      <dl className="mt-4 grid gap-3 border-t border-line pt-4 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">Coverage</dt>
          <dd className="mt-0.5 text-ink">
            {coverage.present} of {coverage.total} factual fields have a value
          </dd>
        </div>
        <div>
          <dt className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">Data mode</dt>
          <dd className="mt-0.5 text-ink">{dataModeLabel(report)}</dd>
        </div>
        <div>
          <dt className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">Request</dt>
          <dd className="mt-0.5 font-mono text-xs text-ink-muted">
            {report.request_id}
            {generated ? <span> · {generated}</span> : null}
          </dd>
        </div>
      </dl>
    </header>
  );
}
