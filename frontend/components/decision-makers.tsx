import { ExternalLink } from "lucide-react";

import type { DecisionMaker, Report } from "@/lib/schema/report";
import { SourceList } from "@/components/source-list";
import { StatusBadge } from "@/components/status-badge";
import { linkDomain, providerLabel, safeExternalUrl } from "@/lib/utils/format";

/** Compact provider/source cell shared by the table and the stacked cards. */
function RowSources({ maker }: { maker: DecisionMaker }) {
  const label = providerLabel(maker.sources);
  const links = (maker.sources ?? []).flatMap((source) => {
    const href = safeExternalUrl(source.url);
    return href ? [href] : [];
  });

  return (
    <div className="flex flex-col items-start gap-1 text-xs text-ink-muted">
      {label ? <span className="font-medium text-ink">{label}</span> : null}
      {links.map((href, index) => (
        <a
          key={`${href}-${index}`}
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-accent-strong hover:underline"
        >
          {linkDomain(href) ?? href}
          <ExternalLink aria-hidden="true" className="h-3 w-3" />
        </a>
      ))}
      {links.length === 0 ? <span>no public link</span> : null}
      {maker.mock ? (
        <span className="rounded-full border border-inferred-line bg-inferred-bg px-1.5 py-0.5 text-[0.65rem] font-semibold text-inferred-ink">
          Sample
        </span>
      ) : null}
    </div>
  );
}

function ProfileLink({ maker }: { maker: DecisionMaker }) {
  const href = safeExternalUrl(maker.profile_url);
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-sm font-medium text-accent-strong hover:underline"
    >
      {linkDomain(href) ?? "Profile"}
      <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
    </a>
  );
}

/**
 * Decision-makers: a table at >=sm and stacked cards below sm, built from the
 * same rows. Columns with no value in any row are hidden. Each row shows its
 * provider/source label, its own status badge, and a "Sample" tag when mock.
 */
export function DecisionMakers({ field }: { field: Report["decision_makers"] }) {
  const rows = field.value ?? [];
  const hasTitle = rows.some((maker) => Boolean(maker.title));
  const hasSeniority = rows.some((maker) => Boolean(maker.seniority));
  const hasProfile = rows.some((maker) => safeExternalUrl(maker.profile_url) !== null);

  return (
    <section
      aria-labelledby="decision-makers-heading"
      data-testid="decision-makers"
      className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="decision-makers-heading" className="text-lg font-semibold text-ink-strong">
          Decision-makers
        </h2>
        <StatusBadge status={field.status} sources={field.sources} />
      </div>

      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-ink-muted">
          No decision-makers with a supported source in this run.
        </p>
      ) : (
        <>
          <div className="mt-4 hidden overflow-x-auto sm:block">
            <table data-testid="decision-makers-table" className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs font-mono uppercase tracking-wide text-ink-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Name
                  </th>
                  {hasTitle ? (
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Title
                    </th>
                  ) : null}
                  {hasSeniority ? (
                    <th scope="col" className="py-2 pr-3 font-medium">
                      Seniority
                    </th>
                  ) : null}
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Source
                  </th>
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Status
                  </th>
                  {hasProfile ? (
                    <th scope="col" className="py-2 font-medium">
                      Profile
                    </th>
                  ) : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((maker, index) => (
                  <tr
                    key={`${maker.name}-${maker.title ?? ""}-${index}`}
                    data-testid="decision-maker-row"
                    className="border-b border-line align-top last:border-b-0"
                  >
                    <th scope="row" className="py-3 pr-3 font-medium text-ink-strong">
                      {maker.name}
                    </th>
                    {hasTitle ? <td className="py-3 pr-3 text-ink">{maker.title}</td> : null}
                    {hasSeniority ? (
                      <td className="py-3 pr-3 text-ink-muted">{maker.seniority}</td>
                    ) : null}
                    <td className="py-3 pr-3">
                      <RowSources maker={maker} />
                    </td>
                    <td className="py-3 pr-3">
                      <StatusBadge status={maker.status} sources={maker.sources} />
                    </td>
                    {hasProfile ? (
                      <td className="py-3">
                        <ProfileLink maker={maker} />
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <ul className="mt-4 space-y-3 sm:hidden">
            {rows.map((maker, index) => (
              <li
                key={`${maker.name}-${maker.title ?? ""}-${index}`}
                data-testid="decision-maker-card"
                className="rounded-card border border-line bg-surface-muted p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="font-medium text-ink-strong">{maker.name}</p>
                  <StatusBadge status={maker.status} sources={maker.sources} />
                </div>
                {maker.title ? <p className="mt-1 text-sm text-ink">{maker.title}</p> : null}
                {maker.seniority ? (
                  <p className="text-xs text-ink-muted">Seniority: {maker.seniority}</p>
                ) : null}
                <div className="mt-2">
                  <RowSources maker={maker} />
                </div>
                {safeExternalUrl(maker.profile_url) ? (
                  <p className="mt-2">
                    <ProfileLink maker={maker} />
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      )}

      <SourceList sources={field.sources} className="mt-4 border-t border-line pt-3" />
    </section>
  );
}
