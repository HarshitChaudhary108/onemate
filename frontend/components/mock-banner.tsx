import { TriangleAlert } from "lucide-react";

import type { Report } from "@/lib/schema/report";
import { dataModeLabel, isMockReport } from "@/lib/utils/format";

/**
 * Persistent, prominent mock/sample banner (spec section 9, main page states).
 * Shown whenever `data_mode !== "live"` or any attached source is mock. It is
 * `role="status"` so assistive tech announces it when a report renders.
 */
export function MockBanner({ report }: { report: Report }) {
  if (!isMockReport(report)) return null;

  return (
    <div
      role="status"
      data-testid="mock-banner"
      className="rounded-card border border-inferred-line bg-inferred-bg px-4 py-3 text-sm text-inferred-ink"
    >
      <p className="flex items-center gap-2 font-semibold">
        <TriangleAlert aria-hidden="true" className="h-4 w-4" />
        Mock / sample data
      </p>
      <p className="mt-1">
        This report was produced with mock providers enabled (explicit configuration, no live
        provider calls). Every record marked “Sample” is synthetic and must not be treated as a
        real contact or a real company fact.
      </p>
      <p className="mt-1 text-xs">Data mode: {dataModeLabel(report)}</p>
    </div>
  );
}
