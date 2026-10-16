/**
 * Loading skeleton: neutral blocks for the report sections. No fake stages,
 * no percentages, no timers — just shape. Hidden from assistive tech because
 * the loading message (aria-live) already announces the state.
 */
export function ReportSkeleton() {
  return (
    <div
      aria-hidden="true"
      data-testid="report-skeleton"
      className="animate-pulse space-y-4"
    >
      <div className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6">
        <div className="h-4 w-24 rounded bg-surface-muted" />
        <div className="mt-3 h-7 w-64 max-w-full rounded bg-surface-muted" />
        <div className="mt-3 h-4 w-40 rounded bg-surface-muted" />
        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <div className="h-10 rounded bg-surface-muted" />
          <div className="h-10 rounded bg-surface-muted" />
          <div className="h-10 rounded bg-surface-muted" />
        </div>
      </div>
      <div className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6">
        <div className="h-5 w-32 rounded bg-surface-muted" />
        <div className="mt-4 space-y-3">
          <div className="h-4 w-full rounded bg-surface-muted" />
          <div className="h-4 w-11/12 rounded bg-surface-muted" />
          <div className="h-4 w-3/4 rounded bg-surface-muted" />
        </div>
      </div>
      <div className="rounded-card border border-line bg-surface p-5 shadow-card sm:p-6">
        <div className="h-5 w-40 rounded bg-surface-muted" />
        <div className="mt-4 space-y-3">
          <div className="h-4 w-full rounded bg-surface-muted" />
          <div className="h-4 w-5/6 rounded bg-surface-muted" />
        </div>
      </div>
    </div>
  );
}
