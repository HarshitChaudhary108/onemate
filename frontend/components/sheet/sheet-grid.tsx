"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import {
  CircleAlert,
  CircleSlash,
  Clock3,
  Search,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type Ref,
} from "react";

import { ColumnHeader } from "@/components/sheet/column-header";
import { SheetCellView } from "@/components/sheet/cell-view";
import type { IdentityStatus, SheetRow } from "@/lib/schema/sheet";
import { cellKey, selectRow, useSheetStore } from "@/lib/store/sheet-store";
import { identityLabel } from "@/lib/utils/cell-format";

/**
 * The sheet grid (P7): virtualized rows (@tanstack/react-virtual), a sticky
 * header, a pinned "Company" column, and arrow-key navigation on a real
 * `role="grid"`. Cells subscribe individually through zustand selectors —
 * a cell event re-renders that cell only.
 *
 * Layout: one scroll container. The header row is `sticky top-0`; the
 * company column is `sticky left-0` inside every row. Rows are absolutely
 * positioned by the virtualizer; columns are fixed 220px (the pinned one
 * 240px) so horizontal position is pure arithmetic.
 */

export const PINNED_WIDTH = 240;
export const COLUMN_WIDTH = 220;
export const ROW_HEIGHT = 36;
export const HEADER_HEIGHT = 40;

const IDENTITY_BADGE: Record<IdentityStatus, { tone: string; icon: LucideIcon }> = {
  pending: {
    tone: "border-reported-line bg-reported-bg text-reported-ink",
    icon: Clock3,
  },
  resolved: {
    tone: "border-verified-line bg-verified-bg text-verified-ink",
    icon: ShieldCheck,
  },
  unresolved: {
    tone: "border-unavailable-line bg-unavailable-bg text-unavailable-ink",
    icon: CircleSlash,
  },
  ambiguous: {
    tone: "border-inferred-line bg-inferred-bg text-inferred-ink",
    icon: TriangleAlert,
  },
  error: {
    tone: "border-danger-line bg-danger-bg text-danger-ink",
    icon: CircleAlert,
  },
};

export type SheetGridApi = {
  /** Return focus to the active grid cell (after Esc closes the inspector). */
  focusActiveCell(): void;
};

type CellPosition = { row: number; col: number };

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

// --- pinned company column ---------------------------------------------------

function IdentityBadge({ status }: { status: IdentityStatus }) {
  const badge = IDENTITY_BADGE[status];
  const Icon = badge.icon;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-px text-[0.65rem] font-medium ${badge.tone}`}
    >
      <Icon aria-hidden="true" className="h-3 w-3" />
      {identityLabel(status)}
    </span>
  );
}

const CompanyCell = memo(function CompanyCell({
  rowId,
  onDeepProfile,
}: {
  rowId: string;
  onDeepProfile?: (rowId: string) => void;
}) {
  const row = useSheetStore(selectRow(rowId));
  return (
    <CompanyCellView row={row} onDeepProfile={() => onDeepProfile?.(rowId)} />
  );
});

const CompanyCellView = memo(function CompanyCellView({
  row,
  onDeepProfile,
}: {
  row: SheetRow | undefined;
  onDeepProfile: () => void;
}) {
  if (row === undefined) {
    return (
      <span className="block h-3 w-3/4 animate-pulse rounded bg-surface-muted" aria-hidden="true" />
    );
  }
  const name = row.resolved_name ?? row.input_name ?? null;
  const domain = row.resolved_domain ?? row.input_domain ?? null;
  const canProfile = name !== null || domain !== null;
  return (
    <span className="group/company flex h-full min-w-0 items-center gap-2 px-2">
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-medium text-ink-strong" title={name ?? undefined}>
          {name ?? "—"}
        </span>
        <span className="block truncate font-mono text-[0.65rem] text-ink-muted" title={domain ?? undefined}>
          {domain ?? ""}
        </span>
      </span>
      <IdentityBadge status={row.identity_status} />
      {canProfile ? (
        <button
          type="button"
          tabIndex={-1}
          onClick={(event) => {
            event.stopPropagation();
            onDeepProfile();
          }}
          title={`Deep profile for ${name ?? domain}`}
          aria-label={`Deep profile for ${name ?? domain}`}
          className="hidden shrink-0 rounded-full border border-line bg-surface p-1 text-ink-muted hover:text-accent group-hover/company:inline-flex group-focus-within/company:inline-flex"
        >
          <Search aria-hidden="true" className="h-3 w-3" />
        </button>
      ) : null}
    </span>
  );
});

// --- row ---------------------------------------------------------------------

type GridRowProps = {
  rowId: string;
  rowIndex: number;
  columnIds: string[];
  /** The column index holding focus in THIS row, or -1 when the active cell
   *  is in another row — keeps arrow-key moves re-rendering just two rows. */
  activeCol: number;
  /** `${rowId}:${columnId}` of the cell open in the inspector, if any. */
  selectedCellKey: string | null;
  newColumns: ReadonlySet<string>;
  registerCell: (key: string, element: HTMLElement | null) => void;
  onFocusCell: (row: number, col: number) => void;
  onInspectCell: (rowId: string, columnId: string) => void;
  onDeepProfile?: (rowId: string) => void;
  onRetryErrors?: () => void;
};

const GridRow = memo(function GridRow({
  rowId,
  rowIndex,
  columnIds,
  activeCol,
  selectedCellKey,
  newColumns,
  registerCell,
  onFocusCell,
  onInspectCell,
  onDeepProfile,
  onRetryErrors,
}: GridRowProps) {
  const cells = columnIds.map((columnId, index) => {
    const colIndex = index + 1; // column 0 is the pinned company column
    const key = `${rowIndex}:${colIndex}`;
    const animate = newColumns.has(columnId);
    const isSelected = selectedCellKey === cellKey(rowId, columnId);
    return (
      <div
        key={columnId}
        ref={(element) => registerCell(key, element)}
        role="gridcell"
        aria-colindex={colIndex + 1}
        tabIndex={activeCol === colIndex ? 0 : -1}
        data-grid-cell=""
        data-row={rowIndex}
        data-col={colIndex}
        data-cell-key={cellKey(rowId, columnId)}
        onFocus={() => onFocusCell(rowIndex, colIndex)}
        onDoubleClick={() => onInspectCell(rowId, columnId)}
        className={`h-full w-[220px] shrink-0 border-r border-line px-0 outline-none ${
          activeCol === colIndex
            ? "shadow-[inset_0_0_0_2px_var(--color-grid-ring)]"
            : ""
        } ${isSelected ? "bg-accent-soft" : ""} ${
          animate ? "animate-column-in" : ""
        }`}
      >
        <div className="flex h-full items-center px-2">
          <SheetCellView rowId={rowId} columnId={columnId} onRetry={onRetryErrors} />
        </div>
      </div>
    );
  });

  return (
    <div role="row" aria-rowindex={rowIndex + 2} className="group flex h-full hover:bg-grid-row-hover">
      <div
        ref={(element) => registerCell(`${rowIndex}:0`, element)}
        role="rowheader"
        aria-colindex={1}
        tabIndex={activeCol === 0 ? 0 : -1}
        data-grid-cell=""
        data-row={rowIndex}
        data-col={0}
        onFocus={() => onFocusCell(rowIndex, 0)}
        className={`sticky left-0 z-10 h-full w-[240px] shrink-0 border-r border-line bg-surface shadow-(--shadow-grid-sticky-col) outline-none group-hover:bg-grid-row-hover ${
          activeCol === 0 ? "shadow-[inset_0_0_0_2px_var(--color-grid-ring)]" : ""
        }`}
      >
        <CompanyCell rowId={rowId} onDeepProfile={onDeepProfile} />
      </div>
      {cells}
    </div>
  );
});

// --- grid --------------------------------------------------------------------

export function SheetGrid({
  onInspectCell,
  onCloseInspector,
  onDeepProfile,
  onRetryErrors,
  selectedCellKey,
  apiRef,
}: {
  onInspectCell: (rowId: string, columnId: string) => void;
  onCloseInspector?: () => void;
  onDeepProfile?: (rowId: string) => void;
  onRetryErrors?: () => void;
  /** `${rowId}:${columnId}` of the cell open in the inspector, if any. */
  selectedCellKey?: string | null;
  apiRef?: Ref<SheetGridApi>;
}) {
  const rowOrder = useSheetStore((state) => state.rowOrder);
  const columnOrder = useSheetStore((state) => state.columnOrder);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const cellElements = useRef(new Map<string, HTMLElement>());
  const [activeCell, setActiveCell] = useState<CellPosition | null>(null);
  const [newColumns, setNewColumns] = useState<ReadonlySet<string>>(new Set());
  const pendingFocus = useRef<CellPosition | null>(null);
  const previousColumns = useRef<string[]>([]);

  const rowVirtualizer = useVirtualizer({
    count: rowOrder.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
    // jsdom (tests) never measures a real viewport — the initial rect keeps
    // the first rows rendered until a real element reports its size.
    initialRect: { width: 1200, height: 600 },
  });

  const totalWidth = PINNED_WIDTH + columnOrder.length * COLUMN_WIDTH;
  const colCount = columnOrder.length + 1;

  const registerCell = useCallback((key: string, element: HTMLElement | null) => {
    if (element === null) cellElements.current.delete(key);
    else cellElements.current.set(key, element);
  }, []);

  const focusCell = useCallback(
    (position: CellPosition) => {
      pendingFocus.current = position;
      setActiveCell(position);
      rowVirtualizer.scrollToIndex(position.row, { align: "auto" });
    },
    [rowVirtualizer],
  );

  // Focus follows the render that made the target cell exist — scrollToIndex
  // can pull the row into the virtual range only on the next paint.
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    const element = cellElements.current.get(`${target.row}:${target.col}`);
    if (element !== undefined) {
      element.focus({ preventScroll: true });
      element.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      pendingFocus.current = null;
    }
  });

  useImperativeHandle(
    apiRef,
    () => ({
      focusActiveCell() {
        if (activeCell === null) return;
        const element = cellElements.current.get(`${activeCell.row}:${activeCell.col}`);
        element?.focus({ preventScroll: true });
      },
    }),
    [activeCell],
  );

  // New columns: mark the appended ids for the slide-in and scroll the right
  // edge into view. Existing columns never move — the animation class goes
  // only on cells that mount with the new column, and it is cleared after
  // one animation length so virtualization remounts stay still. Reduced
  // motion collapses the keyframes via the global media rule.
  useEffect(() => {
    const previous = previousColumns.current;
    previousColumns.current = columnOrder;
    if (previous.length === 0) return; // initial load is not an animation
    const added = columnOrder.filter((id) => !previous.includes(id));
    if (added.length === 0) return;
    setNewColumns(new Set(added));
    const scroller = scrollRef.current;
    if (scroller !== null) {
      const left = scroller.scrollWidth;
      if (typeof scroller.scrollTo === "function") {
        scroller.scrollTo({ left, behavior: prefersReducedMotion() ? "auto" : "smooth" });
      } else {
        scroller.scrollLeft = left;
      }
    }
    const timer = setTimeout(() => setNewColumns(new Set()), 300);
    return () => clearTimeout(timer);
  }, [columnOrder]);

  const handleKeyDown = useCallback(
    (event: ReactKeyboardEvent) => {
      if (activeCell === null) return;
      const rowMax = rowOrder.length - 1;
      const colMax = columnOrder.length; // +1 for the pinned company column
      const move = (row: number, col: number) => {
        event.preventDefault();
        focusCell({
          row: Math.max(0, Math.min(rowMax, row)),
          col: Math.max(0, Math.min(colMax, col)),
        });
      };
      switch (event.key) {
        case "ArrowUp":
          move(activeCell.row - 1, activeCell.col);
          break;
        case "ArrowDown":
          move(activeCell.row + 1, activeCell.col);
          break;
        case "ArrowLeft":
          move(activeCell.row, activeCell.col - 1);
          break;
        case "ArrowRight":
          move(activeCell.row, activeCell.col + 1);
          break;
        case "Home":
          move(activeCell.row, 0);
          break;
        case "End":
          move(activeCell.row, colMax);
          break;
        case "Enter": {
          const rowId = rowOrder[activeCell.row];
          if (activeCell.col === 0) {
            // Enter on the pinned company cell runs the row's deep profile —
            // the keyboard path to the same action the hover button exposes.
            if (rowId !== undefined) {
              event.preventDefault();
              onDeepProfile?.(rowId);
            }
            return;
          }
          const columnId = columnOrder[activeCell.col - 1];
          if (rowId !== undefined && columnId !== undefined) {
            event.preventDefault();
            onInspectCell(rowId, columnId);
          }
          break;
        }
        case "Escape": {
          event.preventDefault();
          onCloseInspector?.();
          break;
        }
      }
    },
    [activeCell, rowOrder, columnOrder, focusCell, onInspectCell, onCloseInspector, onDeepProfile],
  );

  const handleFocusCell = useCallback((row: number, col: number) => {
    setActiveCell({ row, col });
  }, []);

  const noRows = rowOrder.length === 0;

  return (
    <div
      ref={scrollRef}
      role="grid"
      aria-label="Sheet"
      aria-rowcount={rowOrder.length + 1}
      aria-colcount={colCount}
      onKeyDown={handleKeyDown}
      className="h-full w-full overflow-auto bg-surface text-[13px] tabular-nums"
      data-testid="sheet-grid"
    >
      <div style={{ width: totalWidth, minWidth: "100%" }}>
        {/* sticky header */}
        <div
          role="row"
          aria-rowindex={1}
          className="sticky top-0 z-20 flex border-b border-line bg-surface shadow-(--shadow-grid-sticky-head)"
          style={{ height: HEADER_HEIGHT, width: totalWidth }}
        >
          <div
            role="columnheader"
            aria-colindex={1}
            className="sticky left-0 z-30 flex w-[240px] shrink-0 items-center border-r border-line bg-surface px-2 shadow-(--shadow-grid-sticky-col)"
          >
            <span className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
              Company
            </span>
          </div>
          {columnOrder.map((columnId, index) => (
            <div
              key={columnId}
              role="columnheader"
              aria-colindex={index + 2}
              className={`h-full w-[220px] shrink-0 border-r border-line ${
                newColumns.has(columnId) ? "animate-column-in" : ""
              }`}
            >
              <ColumnHeader columnId={columnId} />
            </div>
          ))}
        </div>

        {noRows ? (
          <div className="px-4 py-8 text-sm text-ink-muted">
            No rows yet — use “Import rows” in the toolbar to paste a company list.
          </div>
        ) : (
          <div
            className="relative"
            style={{ height: rowVirtualizer.getTotalSize(), width: totalWidth }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const rowId = rowOrder[virtualRow.index];
              if (rowId === undefined) return null;
              return (
                <div
                  key={rowId}
                  className="absolute left-0 top-0 border-b border-line"
                  style={{
                    transform: `translateY(${virtualRow.start}px)`,
                    height: ROW_HEIGHT,
                    width: totalWidth,
                  }}
                >
                  <GridRow
                    rowId={rowId}
                    rowIndex={virtualRow.index}
                    columnIds={columnOrder}
                    // Roving tabindex needs one tabbable cell before any
                    // cell has focus — row 0's pinned company cell is the
                    // keyboard entry point (Tab in, then arrows).
                    activeCol={
                      activeCell === null
                        ? virtualRow.index === 0
                          ? 0
                          : -1
                        : activeCell.row === virtualRow.index
                          ? activeCell.col
                          : -1
                    }
                    selectedCellKey={selectedCellKey ?? null}
                    newColumns={newColumns}
                    registerCell={registerCell}
                    onFocusCell={handleFocusCell}
                    onInspectCell={onInspectCell}
                    onDeepProfile={onDeepProfile}
                    onRetryErrors={onRetryErrors}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
