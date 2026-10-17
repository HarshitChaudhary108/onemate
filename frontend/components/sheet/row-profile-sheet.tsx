"use client";

import { X } from "lucide-react";
import { useEffect, useState } from "react";

import { ErrorAlert } from "@/components/error-alert";
import { ReportSkeleton } from "@/components/report-skeleton";
import { ReportView } from "@/components/report-view";
import { researchCompany } from "@/lib/api/client";
import type { Report } from "@/lib/schema/report";
import { selectRow, useSheetStore } from "@/lib/store/sheet-store";

/**
 * Row "Deep profile" (P7 / plan §4.6): a side sheet that runs the existing
 * `researchCompany` flow for the row's company and renders the existing
 * ReportView. It only ever fires from the user's explicit action — nothing
 * runs on grid mount or scroll.
 */

type State =
  | { kind: "loading" }
  | { kind: "report"; report: Report }
  | {
      kind: "error";
      status: number | null;
      code: string;
      message: string;
      requestId: string | null;
    };

export function RowProfileSheet({
  rowId,
  onClose,
}: {
  rowId: string;
  onClose: () => void;
}) {
  const row = useSheetStore(selectRow(rowId));
  const [state, setState] = useState<State>({ kind: "loading" });

  const name = row?.resolved_name ?? row?.input_name ?? undefined;
  const domain = row?.resolved_domain ?? row?.input_domain ?? undefined;

  useEffect(() => {
    let cancelled = false;
    void researchCompany({ company_name: name, domain }).then((result) => {
      if (cancelled) return;
      switch (result.kind) {
        case "report":
          setState({ kind: "report", report: result.report });
          break;
        case "network":
          setState({
            kind: "error",
            status: null,
            code: "network",
            message:
              "Could not reach the research service. Check that the backend is running.",
            requestId: null,
          });
          break;
        case "invalid_response":
          setState({
            kind: "error",
            status: null,
            code: "invalid_response",
            message:
              "The research service returned a response this app could not read.",
            requestId: null,
          });
          break;
        case "validation":
          setState({
            kind: "error",
            status: 422,
            code: "validation",
            message: "The row's company details were rejected by the backend.",
            requestId: null,
          });
          break;
        case "api_error":
          setState({
            kind: "error",
            status: result.status,
            code: result.code,
            message: result.message,
            requestId: result.requestId,
          });
          break;
      }
    });
    return () => {
      cancelled = true;
    };
    // Run once per open — the row identity arriving later does not re-run it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowId]);

  return (
    <div className="flex h-full w-[28rem] shrink-0 flex-col border-l border-line bg-page" data-testid="row-profile-sheet">
      <div className="flex items-start justify-between gap-2 border-b border-line bg-surface px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink-strong">
            Deep profile{name ? `: ${name}` : ""}
          </p>
          <p className="truncate text-xs text-ink-muted">
            {domain ?? "The full research report for this row."}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close deep profile"
          className="rounded p-1 text-ink-muted hover:bg-surface-muted hover:text-ink"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {state.kind === "loading" ? (
          <section aria-label="Research in progress">
            <p role="status" className="text-sm font-medium text-ink">
              Researching company…
            </p>
            <div className="mt-4">
              <ReportSkeleton />
            </div>
          </section>
        ) : null}
        {state.kind === "error" ? (
          <ErrorAlert
            status={state.status}
            code={state.code}
            message={state.message}
            requestId={state.requestId}
          />
        ) : null}
        {state.kind === "report" ? <ReportView report={state.report} /> : null}
      </div>
    </div>
  );
}
