"use client";

import { useCallback, useState } from "react";

import { ErrorAlert } from "@/components/error-alert";
import { ReportSkeleton } from "@/components/report-skeleton";
import { ReportView } from "@/components/report-view";
import { ResearchForm } from "@/components/research-form";
import { researchCompany, type ResearchInput, type Result } from "@/lib/api/client";
import type { Report } from "@/lib/schema/report";

/**
 * Client state machine: idle | loading | report | error. Validation (422)
 * field errors are handled inside the form; this component only owns the
 * page-level states. The loading state is a single truthful message —
 * "Researching company…" — plus a skeleton. No fake stages, no percentages.
 */

type ViewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "report"; report: Report }
  | {
      kind: "error";
      status: number | null;
      code: string;
      message: string;
      requestId: string | null;
    };

const NETWORK_MESSAGE =
  "Could not reach the research service. Check that the backend is running and that NEXT_PUBLIC_API_BASE_URL points at it, then try again.";
const INVALID_RESPONSE_MESSAGE =
  "The research service returned a response this app could not read, so no report is shown. Nothing was invented to fill the gap.";

export default function Home() {
  const [state, setState] = useState<ViewState>({ kind: "idle" });

  const handleSubmit = useCallback(async (input: ResearchInput): Promise<Result> => {
    setState({ kind: "loading" });
    const result = await researchCompany(input);
    switch (result.kind) {
      case "report":
        setState({ kind: "report", report: result.report });
        break;
      case "validation":
        // Field errors are rendered by the form itself; back to the form.
        setState({ kind: "idle" });
        break;
      case "network":
        setState({
          kind: "error",
          status: null,
          code: "network",
          message: NETWORK_MESSAGE,
          requestId: null,
        });
        break;
      case "invalid_response":
        setState({
          kind: "error",
          status: null,
          code: "invalid_response",
          message: INVALID_RESPONSE_MESSAGE,
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
    return result;
  }, []);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight text-ink-strong">Outmate</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Focused B2B company research: submit a company name or website and inspect an
          evidence-backed report — every claim carries its provider source, and anything the run
          could not support stays explicitly unavailable.
        </p>
      </header>

      <ResearchForm pending={state.kind === "loading"} onSubmit={handleSubmit} />

      <div className="mt-6">
        {state.kind === "loading" ? (
          <section aria-label="Research in progress">
            <p aria-live="polite" role="status" className="text-sm font-medium text-ink">
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
