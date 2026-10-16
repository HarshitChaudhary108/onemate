"use client";

import { ContactInfo } from "@/components/contact-info";
import { DecisionMakers } from "@/components/decision-makers";
import { GrowthSignals } from "@/components/growth-signals";
import { IdentityNotice } from "@/components/identity-notice";
import { JsonViewer } from "@/components/json-viewer";
import { MissingFields } from "@/components/missing-fields";
import { MockBanner } from "@/components/mock-banner";
import { OpportunityHypothesis } from "@/components/opportunity-hypothesis";
import { OverviewSection } from "@/components/overview-section";
import { ProviderStatus } from "@/components/provider-status";
import { ReportHeader } from "@/components/report-header";
import { SourcesPanel } from "@/components/sources-panel";
import type { Report } from "@/lib/schema/report";

/**
 * Composed report: header + main column + evidence rail. A resolved identity
 * renders the full sections; an unresolved/no-evidence run renders the
 * explanation instead and skips the sections that have nothing to show.
 * Every section hides absent values rather than rendering placeholders.
 */
export function ReportView({ report }: { report: Report }) {
  const resolved = report.company_identity.resolved;

  return (
    <div className="space-y-5">
      <MockBanner report={report} />
      <ReportHeader report={report} />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-5">
          {resolved ? (
            <>
              <OverviewSection report={report} />
              <GrowthSignals field={report.growth_signals} />
              <DecisionMakers field={report.decision_makers} />
              <ContactInfo field={report.contact_information} />
              <OpportunityHypothesis field={report.potential_gtm_opportunity} />
            </>
          ) : (
            <IdentityNotice report={report} />
          )}
          <MissingFields report={report} />
          <ProviderStatus report={report} />
          <JsonViewer report={report} />
        </div>
        <div className="min-w-0 lg:sticky lg:top-6 lg:self-start">
          <SourcesPanel report={report} />
        </div>
      </div>
    </div>
  );
}
