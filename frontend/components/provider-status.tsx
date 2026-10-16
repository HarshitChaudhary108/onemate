import type { ProviderSummary, Report } from "@/lib/schema/report";
import { humanizeToken, recoveryHint, usageEntries } from "@/lib/utils/format";

function ProviderCard({ name, summary }: { name: string; summary: ProviderSummary }) {
  const usage = usageEntries(summary.usage);
  return (
    <li data-testid="provider-card" className="rounded-card border border-line bg-surface-muted p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-ink-strong">{name}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-full border border-reported-line bg-reported-bg px-2 py-0.5 font-medium text-reported-ink">
            {humanizeToken(summary.state)}
          </span>
          {summary.mock ? (
            <span className="rounded-full border border-inferred-line bg-inferred-bg px-2 py-0.5 font-semibold text-inferred-ink">
              Mock mode
            </span>
          ) : null}
        </div>
      </div>
      <dl className="mt-2 space-y-1 text-xs text-ink-muted">
        <div className="flex gap-2">
          <dt>Calls this run:</dt>
          <dd className="text-ink">{summary.calls}</dd>
        </div>
        {usage.length > 0 ? (
          <div>
            <dt>Provider usage (as returned):</dt>
            <dd>
              <ul className="mt-0.5 space-y-0.5">
                {usage.map((entry) => (
                  <li key={entry.key}>
                    <span className="font-medium text-ink">{entry.key}</span>: {entry.value}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        ) : null}
        {summary.note ? (
          <div className="flex gap-2">
            <dt>Note:</dt>
            <dd className="text-ink">{summary.note}</dd>
          </div>
        ) : null}
      </dl>
    </li>
  );
}

/**
 * Provider status and error detail: state, call counts, usage only when the
 * provider returned it, mock flags, each error's message plus a plain
 * recovery hint derived from its stable code, and the request id.
 */
export function ProviderStatus({ report }: { report: Report }) {
  return (
    <section
      aria-labelledby="providers-heading"
      data-testid="provider-status"
      className="rounded-card border border-line bg-surface p-5 shadow-card"
    >
      <h2 id="providers-heading" className="text-lg font-semibold text-ink-strong">
        Provider status
      </h2>

      <ul className="mt-4 space-y-3">
        <ProviderCard name="Exa" summary={report.providers.exa} />
        <ProviderCard name="MoltSets" summary={report.providers.moltsets} />
      </ul>

      {report.errors.length > 0 ? (
        <div className="mt-4">
          <h3 className="text-xs font-medium uppercase tracking-wide text-ink-muted">
            Errors this run
          </h3>
          <ul className="mt-2 space-y-3">
            {report.errors.map((error, index) => {
              const hint = recoveryHint(error.code);
              return (
                <li
                  key={`${error.provider}-${error.stage}-${error.code}-${index}`}
                  data-testid="report-error"
                  className="rounded-card border border-danger-line bg-danger-bg p-3 text-sm text-danger-ink"
                >
                  <p className="text-xs font-medium uppercase tracking-wide">
                    {error.provider} · {humanizeToken(error.stage)}
                  </p>
                  <p className="mt-1">{error.message}</p>
                  {hint ? <p className="mt-1 text-xs">What you can do: {hint}</p> : null}
                  <p className="mt-1 font-mono text-[0.7rem]">
                    code: {error.code}
                    {error.retryable ? " · retryable" : " · not retryable"}
                    {error.request_id ? ` · request ${error.request_id}` : ""}
                  </p>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      <p className="mt-4 border-t border-line pt-3 font-mono text-xs text-ink-muted">
        request_id: {report.request_id}
      </p>
    </section>
  );
}
