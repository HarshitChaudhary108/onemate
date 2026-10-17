"use client";

import {
  Ban,
  CircleSlash,
  Clock3,
  Landmark,
  Lightbulb,
  LoaderCircle,
  RotateCcw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { memo } from "react";

import type { Status } from "@/lib/schema/report";
import type { OutputType, SheetCell } from "@/lib/schema/sheet";
import { cellKey, useSheetStore } from "@/lib/store/sheet-store";
import { cellStateLabel, formatCellValue, statusMarkLabel } from "@/lib/utils/cell-format";

/**
 * One grid cell (P7). Every state is icon + text — never colour alone:
 * queued is a muted skeleton, running a spinner, done the value with a
 * small status mark reusing the four status palettes, error a retry
 * control, blocked names the upstream column, stale an amber dot.
 *
 * `SheetCell` is the store-connected wrapper: it subscribes to exactly one
 * cell through a zustand selector, so a cell event re-renders only this
 * cell — never the grid.
 */

const STATUS_MARK: Record<Status, { tone: string; icon: LucideIcon }> = {
  verified: {
    tone: "border-verified-line bg-verified-bg text-verified-ink",
    icon: ShieldCheck,
  },
  "provider-reported": {
    tone: "border-reported-line bg-reported-bg text-reported-ink",
    icon: Landmark,
  },
  inferred: {
    tone: "border-inferred-line bg-inferred-bg text-inferred-ink",
    icon: Lightbulb,
  },
  unavailable: {
    tone: "border-unavailable-line bg-unavailable-bg text-unavailable-ink",
    icon: CircleSlash,
  },
};

function StatusMark({ status }: { status: Status }) {
  const mark = STATUS_MARK[status];
  const Icon = mark.icon;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-px text-[0.65rem] font-medium ${mark.tone}`}
    >
      <Icon aria-hidden="true" className="h-3 w-3" />
      {statusMarkLabel(status)}
    </span>
  );
}

export const CellView = memo(function CellView({
  cell,
  outputType,
  onRetry,
}: {
  cell: SheetCell | undefined;
  outputType: OutputType;
  onRetry?: () => void;
}) {
  const status = cell?.status ?? "idle";

  if (status === "idle") {
    return (
      <span className="text-ink-muted" data-state="idle">
        <span aria-hidden="true">—</span>
        <span className="sr-only">{cellStateLabel("idle")}</span>
      </span>
    );
  }

  if (status === "queued") {
    return (
      <span className="flex items-center gap-1.5" data-state="queued">
        <Clock3 aria-hidden="true" className="h-3 w-3 shrink-0 text-ink-muted" />
        <span aria-hidden="true" className="h-2 w-3/5 animate-pulse rounded bg-surface-muted" />
        <span className="sr-only">{cellStateLabel("queued")}</span>
      </span>
    );
  }

  if (status === "running") {
    return (
      <span className="flex items-center gap-1.5 text-ink-muted" data-state="running">
        <LoaderCircle aria-hidden="true" className="h-3 w-3 shrink-0 animate-spin" />
        <span className="text-xs">{cellStateLabel("running")}</span>
      </span>
    );
  }

  if (status === "blocked") {
    const reason = cell?.error ?? "blocked by an upstream column";
    return (
      <span
        className="flex min-w-0 max-w-full items-center gap-1.5 text-ink-muted"
        data-state="blocked"
        title={reason}
      >
        <Ban aria-hidden="true" className="h-3 w-3 shrink-0" />
        <span className="min-w-0 truncate text-xs">{reason}</span>
      </span>
    );
  }

  if (status === "error") {
    const message = cell?.error ?? "the cell run failed";
    return (
      <span
        className="flex min-w-0 max-w-full items-center gap-1.5 text-danger-ink"
        data-state="error"
        title={message}
      >
        <TriangleAlert aria-hidden="true" className="h-3 w-3 shrink-0" />
        <span className="min-w-0 truncate text-xs">{message}</span>
        {onRetry ? (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onRetry();
            }}
            title="Start a run that retries every error cell"
            className="inline-flex shrink-0 items-center gap-1 rounded-full border border-danger-line bg-danger-bg px-1.5 py-px text-[0.65rem] font-medium hover:bg-danger-line/40"
          >
            <RotateCcw aria-hidden="true" className="h-3 w-3" />
            Retry
          </button>
        ) : null}
      </span>
    );
  }

  // done — value plus the small field-status mark.
  const result = cell?.result;
  const fieldStatus = result?.status ?? "unavailable";
  const text = formatCellValue(result?.value, outputType);
  const mock = (result?.sources ?? []).some((source) => source.mock);
  return (
    <span className="flex min-w-0 max-w-full items-center gap-1.5" data-state="done">
      <span
        className={`min-w-0 truncate ${outputType === "number" ? "tabular-nums" : ""} ${
          text === null ? "text-ink-muted" : "text-ink"
        }`}
        title={text ?? undefined}
      >
        {text ?? "—"}
      </span>
      {cell?.stale ? (
        <span
          className="inline-flex shrink-0 items-center gap-1 text-[0.65rem] font-medium text-inferred-ink"
          title="Stale — the column prompt changed after this value was written"
        >
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-amber-500" />
          <span className="sr-only">Stale</span>
        </span>
      ) : null}
      <StatusMark status={fieldStatus} />
      {mock ? <span className="sr-only">Includes mock provenance</span> : null}
    </span>
  );
});

/** Store-connected cell: subscribes to its own cell and its column's output
 *  type — two primitive-ref selectors, so a cell event re-renders only this
 *  cell, never the grid. */
export const SheetCellView = memo(function SheetCellView({
  rowId,
  columnId,
  onRetry,
}: {
  rowId: string;
  columnId: string;
  onRetry?: () => void;
}) {
  const cell = useSheetStore((state) => state.cells[cellKey(rowId, columnId)]);
  const outputType = useSheetStore(
    (state) => state.columnsById[columnId]?.output_type ?? "text",
  );
  return <CellView cell={cell} outputType={outputType} onRetry={onRetry} />;
});
