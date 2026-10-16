import { ExternalLink } from "lucide-react";

import type { Report } from "@/lib/schema/report";
import {
  collectSources,
  dedupeSources,
  formatDate,
  formatDateTime,
  linkDomain,
  safeExternalUrl,
} from "@/lib/utils/format";

/**
 * Global evidence panel: every source in the report, deduplicated by
 * URL + provider for display. It supplements the inline claim-level sources
 * and never replaces them. It lives in the right rail on lg+ and stacks below
 * the report on smaller screens.
 */
export function SourcesPanel({ report }: { report: Report }) {
  const sources = dedupeSources(collectSources(report));
  if (sources.length === 0) return null;

  return (
    <section
      aria-labelledby="sources-heading"
      data-testid="sources-panel"
      className="rounded-card border border-line bg-surface p-5 shadow-card"
    >
      <h2 id="sources-heading" className="text-lg font-semibold text-ink-strong">
        Sources
      </h2>
      <p className="mt-1 text-xs text-ink-muted">
        {sources.length} {sources.length === 1 ? "source" : "sources"} in this run.
      </p>
      <ul className="mt-4 space-y-4">
        {sources.map((source, index) => {
          const href = safeExternalUrl(source.url);
          const domain = linkDomain(source.url);
          const published = formatDate(source.published_date);
          const retrieved = formatDateTime(source.retrieved_at);
          return (
            <li
              key={`${source.provider}-${href ?? "no-url"}-${index}`}
              data-testid="source-panel-item"
              className="border-t border-line pt-3 first:border-t-0 first:pt-0"
            >
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                <span className="font-medium text-ink">{source.provider}</span>
                {source.mock ? (
                  <span className="rounded-full border border-inferred-line bg-inferred-bg px-1.5 py-0.5 text-[0.65rem] font-semibold text-inferred-ink">
                    Sample
                  </span>
                ) : null}
                {published ? <span>· {published}</span> : null}
                {retrieved ? <span>· retrieved {retrieved}</span> : null}
              </div>
              {source.title ? (
                <p className="mt-1 text-sm font-medium text-ink-strong">{source.title}</p>
              ) : null}
              {href && domain ? (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-flex items-center gap-1 text-sm font-medium text-accent-strong hover:underline"
                >
                  {domain}
                  <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
                </a>
              ) : (
                <p className="mt-1 text-sm text-ink-muted">No public link returned by this provider.</p>
              )}
              {source.highlight ? (
                <p className="mt-1 text-sm leading-relaxed text-ink">“{source.highlight}”</p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
