import { ExternalLink } from "lucide-react";

import type { Source } from "@/lib/schema/report";
import { formatDate, linkDomain, safeExternalUrl } from "@/lib/utils/format";

/**
 * Claim-level evidence list. Every external link is re-checked on the client
 * (`new URL`, http/https only) and rendered with `rel="noopener noreferrer"`;
 * the visible link text is the link's domain. Sources without a URL are shown
 * as provider-only rows — never as links. Provider text is rendered as plain
 * text.
 */
export function SourceList({
  sources,
  label = "Sources",
  className,
}: {
  sources?: Source[];
  label?: string;
  className?: string;
}) {
  const items = sources ?? [];
  if (items.length === 0) return null;

  return (
    <div className={className}>
      <p className="text-xs font-mono font-medium uppercase tracking-wide text-ink-muted">{label}</p>
      <ul className="mt-1 space-y-1.5">
        {items.map((source, index) => {
          const href = safeExternalUrl(source.url);
          const domain = linkDomain(source.url);
          const published = formatDate(source.published_date);
          return (
            <li
              key={`${source.provider}-${href ?? "no-url"}-${index}`}
              data-testid="source-item"
              className="text-xs text-ink-muted"
            >
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                {href && domain ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 font-medium text-accent-strong hover:underline"
                  >
                    {source.title ?? domain}
                    <ExternalLink aria-hidden="true" className="h-3 w-3" />
                  </a>
                ) : (
                  <span className="font-medium text-ink">{source.provider} source</span>
                )}
                {href && domain ? <span className="text-ink-muted">{domain}</span> : null}
                <span>{source.provider}</span>
                {published ? <span>· {published}</span> : null}
                {href ? null : <span>· no public link</span>}
                {source.mock ? (
                  <span className="rounded-full border border-inferred-line bg-inferred-bg px-1.5 py-0.5 text-[0.65rem] font-semibold text-inferred-ink">
                    Sample
                  </span>
                ) : null}
              </span>
              {source.highlight ? (
                <span className="mt-0.5 block text-ink">“{source.highlight}”</span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
