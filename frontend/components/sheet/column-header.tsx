"use client";

import { memo } from "react";

import { selectColumn, useSheetStore } from "@/lib/store/sheet-store";
import { humanizeToken } from "@/lib/utils/format";

/**
 * Grid column header (P7): the column's name plus its output type, one
 * store subscription per column so a column edit re-renders only its own
 * header — never the grid.
 */
export const ColumnHeader = memo(function ColumnHeader({
  columnId,
}: {
  columnId: string;
}) {
  const column = useSheetStore(selectColumn(columnId));
  if (column === undefined) {
    return (
      <span className="block h-full animate-pulse rounded bg-surface-muted" aria-hidden="true" />
    );
  }
  return (
    <span className="flex h-full items-center justify-between gap-2 px-2">
      <span className="min-w-0 truncate font-medium text-ink-strong" title={column.name}>
        {column.name}
      </span>
      <span className="shrink-0 font-mono text-[0.65rem] uppercase tracking-wide text-ink-muted">
        {humanizeToken(column.output_type)}
      </span>
    </span>
  );
});
