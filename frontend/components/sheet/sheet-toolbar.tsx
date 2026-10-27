"use client";

import { Download, ListPlus, OctagonX, Search, Upload } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { exportCsvUrl } from "@/lib/api/sheets";
import { useSheetStore } from "@/lib/store/sheet-store";
import { formatUsd } from "@/lib/utils/cell-format";

/**
 * Sheet toolbar (P7 / plan §4.6): sheet name, true row/column counts, run
 * progress as TRUE counts ("38 of 60 cells" — never stages or percentages),
 * spend summed only from provider-returned per-cell `costDollars`, and the
 * Cancel and Export controls.
 *
 * Run membership comes from `cell.run_id`: the backend stamps it when a run
 * queues the cell, and the store's run-event resnapshot lands it. A cell
 * that still has to run shows as queued; cancelled queued cells lose their
 * run_id and drop out of the denominator — so both numbers stay true.
 */

const TERMINAL = new Set(["done", "error", "blocked"]);

export function SheetToolbar({
  onCancelRun,
  onImportRows,
  onFindCompanies,
  onPushToHubSpot,
  cancelling,
}: {
  onCancelRun: (runId: string) => void;
  onImportRows: () => void;
  onFindCompanies: () => void;
  onPushToHubSpot?: () => void;
  cancelling?: boolean;
}) {
  const sheet = useSheetStore((state) => state.sheet);
  const rowCount = useSheetStore((state) => state.rowOrder.length);
  const columnCount = useSheetStore((state) => state.columnOrder.length);
  const latestRun = useSheetStore((state) => state.latestRun);
  const runStates = useSheetStore((state) => state.runStates);
  const cells = useSheetStore((state) => state.cells);
  const connection = useSheetStore((state) => state.connection);

  // The run events say is live beats the snapshot's latest_run — a fresh
  // run can start before the resnapshot lands.
  const startedRunId = useMemo(() => {
    for (const [id, state] of Object.entries(runStates)) {
      if (state.status === "started") return id;
    }
    return null;
  }, [runStates]);

  const runId = startedRunId ?? latestRun?.id ?? null;
  const runActive = startedRunId !== null;
  const runStopped = runId !== null && runStates[runId]?.status === "stopped";
  const cap = runId !== null && latestRun?.id === runId ? (latestRun.cap ?? null) : null;

  const progress = useMemo(() => {
    if (runId === null) return null;
    let total = 0;
    let finished = 0;
    let spent = 0;
    let hasCost = false;
    for (const cell of Object.values(cells)) {
      if (cell.run_id !== runId) continue;
      total += 1;
      if (TERMINAL.has(cell.status)) finished += 1;
      if (typeof cell.cost === "number") {
        spent += cell.cost;
        hasCost = true;
      }
    }
    return { total, finished, spent: hasCost ? spent : null };
  }, [cells, runId]);

  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface px-4 py-2.5"
      data-testid="sheet-toolbar"
    >
      <Link
        href="/"
        className="text-xs font-medium text-accent underline-offset-2 hover:underline"
      >
        ← Sheets
      </Link>
      <h1 className="min-w-0 truncate text-base font-semibold tracking-tight text-ink-strong">
        {sheet?.name ?? "Sheet"}
      </h1>
      <p className="text-xs text-ink-muted">
        {rowCount} {rowCount === 1 ? "row" : "rows"} · {columnCount}{" "}
        {columnCount === 1 ? "column" : "columns"}
      </p>

      {connection === "reconnecting" ? (
        <p role="status" className="text-xs text-inferred-ink">
          Reconnecting to live updates…
        </p>
      ) : null}

      <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
        {progress !== null && (runActive || progress.finished > 0) ? (
          <p className="text-xs tabular-nums text-ink" data-testid="run-progress">
            {progress.finished} of {progress.total} cells
            {runStopped && runStates[runId]?.reason ? (
              <span className="text-ink-muted"> · stopped: {runStates[runId].reason}</span>
            ) : null}
          </p>
        ) : null}
        {progress?.spent !== null && progress?.spent !== undefined ? (
          <p className="text-xs tabular-nums text-ink-muted" title="Sum of provider-reported costDollars">
            Spent {formatUsd(progress.spent)}
            {cap != null ? ` of ${formatUsd(cap)} cap` : ""}
          </p>
        ) : null}

        {runActive && runId !== null ? (
          <button
            type="button"
            onClick={() => onCancelRun(runId)}
            disabled={cancelling}
            className="inline-flex items-center gap-1.5 rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-60"
          >
            <OctagonX aria-hidden="true" className="h-3.5 w-3.5" />
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        ) : null}

        <button
          type="button"
          onClick={onFindCompanies}
          className="inline-flex items-center gap-1.5 rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
        >
          <Search aria-hidden="true" className="h-3.5 w-3.5" />
          Find companies
        </button>

        <button
          type="button"
          onClick={onImportRows}
          className="inline-flex items-center gap-1.5 rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
        >
          <ListPlus aria-hidden="true" className="h-3.5 w-3.5" />
          Import rows
        </button>

        {onPushToHubSpot !== undefined ? (
          <button
            type="button"
            onClick={onPushToHubSpot}
            className="inline-flex items-center gap-1.5 rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            <Upload aria-hidden="true" className="h-3.5 w-3.5" />
            Push to HubSpot
          </button>
        ) : null}

        {sheet !== null ? (
          <a
            href={exportCsvUrl(sheet.id)}
            download
            className="inline-flex items-center gap-1.5 rounded-card border border-line-strong bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            <Download aria-hidden="true" className="h-3.5 w-3.5" />
            Export CSV
          </a>
        ) : null}
      </div>
    </div>
  );
}
