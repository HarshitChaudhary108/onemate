/**
 * Normalized sheet store (plan §4.5 client protocol / P6, D10).
 *
 * The backend protocol is: subscribe to SSE FIRST, then fetch the
 * snapshot, then merge per cell by revision — last write wins, no replay.
 * Cells are keyed `"${rowId}:${columnId}"`, rows and columns by id with
 * position arrays for ordering.
 *
 * The two wrinkles this store handles:
 *  - Cell events carry only {status, revision, stale} — never the result.
 *    An accepted terminal event therefore means "fetch the cell detail";
 *    `applyEvents` reports which events need that fetch (`fetchCells`).
 *  - Events can arrive while a snapshot fetch is in flight (always after
 *    subscribe, again on every reconnect). They are buffered and merged
 *    AFTER the snapshot lands, so snapshot-vs-event races resolve by
 *    revision, not arrival order.
 *
 * `connect`/`disconnect` own the subscription so components never manage
 * one in an effect — a sheet page calls `connect(sheetId)` once.
 */

import { create } from "zustand";

import {
  subscribeSheetEvents,
  type CellEvent,
  type EventSourceFactory,
  type SheetEvent,
  type SheetSubscription,
} from "@/lib/api/events";
import {
  getCellDetail,
  getSnapshot,
  type SheetResult,
} from "@/lib/api/sheets";
import type {
  CellDetail,
  CellStatus,
  Sheet,
  SheetCell,
  SheetColumn,
  SheetRow,
  SheetRun,
  SheetSnapshot,
} from "@/lib/schema/sheet";
import type { RunEvent } from "@/lib/api/events";

export const cellKey = (rowId: string, columnId: string) => `${rowId}:${columnId}`;

export type ConnectionState =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting"
  | "unavailable";

/** Statuses that carry a payload worth a cell-detail fetch: `done` has a
 *  result, `error`/`blocked` carry the error text. `mark_blocked` on the
 *  backend does not bump the revision, so it is included here. */
const DETAIL_STATUSES: ReadonlySet<CellStatus> = new Set(["done", "error", "blocked"]);

export type ApplyResult = {
  /** Accepted cell events that warrant a cell-detail fetch. */
  fetchCells: CellEvent[];
  /** At least one row event named a row we do not know — resnapshot. */
  unknownRows: boolean;
};

export type SheetConnectDeps = {
  fetchSnapshot?: (sheetId: string) => Promise<SheetResult<SheetSnapshot>>;
  fetchCellDetail?: (
    sheetId: string,
    rowId: string,
    columnId: string,
  ) => Promise<SheetResult<CellDetail>>;
  createEventSource?: EventSourceFactory;
};

type SheetStoreState = {
  sheetId: string | null;
  sheet: Sheet | null;
  rowsById: Record<string, SheetRow>;
  rowOrder: string[];
  columnsById: Record<string, SheetColumn>;
  columnOrder: string[];
  cells: Record<string, SheetCell>;
  latestRun: SheetRun | null;
  /** Per-run live status from run events (no revision — last arrival wins). */
  runStates: Record<string, { status: RunEvent["status"]; reason: string | null }>;
  hydrated: boolean;
  connection: ConnectionState;
  error: string | null;

  loadSnapshot(snapshot: SheetSnapshot): ApplyResult;
  applyEvents(events: SheetEvent[]): ApplyResult;
  upsertCell(cell: SheetCell): void;
  connect(sheetId: string, deps?: SheetConnectDeps): void;
  disconnect(): void;
};

const EMPTY: ApplyResult = { fetchCells: [], unknownRows: false };

function initialState() {
  return {
    sheetId: null,
    sheet: null,
    rowsById: {},
    rowOrder: [],
    columnsById: {},
    columnOrder: [],
    cells: {},
    latestRun: null,
    runStates: {},
    hydrated: false,
    connection: "idle",
    error: null,
  } satisfies Partial<SheetStoreState>;
}

/** Merge one event into mutable draft maps. Events on unknown cells are
 *  reported for a detail fetch (the full cell comes back from the API);
 *  events on unknown rows flag a resnapshot (there is no row endpoint). */
function mergeEvent(
  cells: Record<string, SheetCell>,
  rowsById: Record<string, SheetRow>,
  runStates: SheetStoreState["runStates"],
  event: SheetEvent,
  out: ApplyResult,
): void {
  if (event.type === "cell") {
    const key = cellKey(event.row_id, event.column_id);
    const existing = cells[key];
    if (existing === undefined) {
      out.fetchCells.push(event);
      return;
    }
    // Strictly-greater revision wins. Same-revision events are dropped:
    // within one revision only transient (idle/queued/running/blocked)
    // states exist, and dropping a same-revision event can never regress
    // a completed write — the next write bumps the revision anyway.
    if (event.revision <= existing.revision) return;
    cells[key] = {
      ...existing,
      status: event.status,
      stale: event.stale,
      revision: event.revision,
    };
    if (DETAIL_STATUSES.has(event.status)) out.fetchCells.push(event);
    return;
  }
  if (event.type === "row") {
    const row = rowsById[event.row_id];
    if (row === undefined) {
      out.unknownRows = true;
      return;
    }
    rowsById[event.row_id] = {
      ...row,
      identity_status: event.identity_status,
      resolved_name: event.resolved_name,
      resolved_domain: event.resolved_domain,
    };
    return;
  }
  runStates[event.run_id] = { status: event.status, reason: event.reason };
}

export const useSheetStore = create<SheetStoreState>()((set, get) => {
  // Not reactive: protocol bookkeeping that must never re-render anything.
  let subscription: SheetSubscription | null = null;
  let pendingEvents: SheetEvent[] = [];
  let snapshotInFlight = 0;

  /** Merge a batch into the live state (no buffering, no guards). */
  const mergeBatch = (events: SheetEvent[]): ApplyResult => {
    const cells = { ...get().cells };
    const rowsById = { ...get().rowsById };
    const runStates = { ...get().runStates };
    const out: ApplyResult = { fetchCells: [], unknownRows: false };
    for (const event of events) mergeEvent(cells, rowsById, runStates, event, out);
    set({ cells, rowsById, runStates });
    return out;
  };

  return {
    ...initialState(),

    loadSnapshot(snapshot) {
      const state = get();
      const cells: Record<string, SheetCell> = {};
      for (const cell of snapshot.cells) {
        const key = cellKey(cell.row_id, cell.column_id);
        const existing = state.cells[key];
        // A re-snapshot read can predate already-merged events — keep the
        // newer revision, never regress.
        cells[key] =
          existing !== undefined && existing.revision > cell.revision ? existing : cell;
      }
      const rows = [...snapshot.rows].sort((a, b) => a.position - b.position);
      const columns = [...snapshot.columns].sort((a, b) => a.position - b.position);
      set({
        sheet: snapshot.sheet,
        rowsById: Object.fromEntries(rows.map((row) => [row.id, row])),
        rowOrder: rows.map((row) => row.id),
        columnsById: Object.fromEntries(columns.map((col) => [col.id, col])),
        columnOrder: columns.map((col) => col.id),
        cells,
        latestRun: snapshot.latest_run ?? null,
        hydrated: true,
        error: null,
      });
      // Drain events buffered while the snapshot was in flight — merged
      // directly (not via applyEvents, whose in-flight guard would just
      // re-buffer them); the revision check still applies.
      const drained = pendingEvents;
      pendingEvents = [];
      return drained.length > 0 ? mergeBatch(drained) : EMPTY;
    },

    applyEvents(events) {
      if (!get().hydrated || snapshotInFlight > 0) {
        pendingEvents.push(...events);
        return EMPTY;
      }
      return mergeBatch(events);
    },

    upsertCell(cell) {
      const key = cellKey(cell.row_id, cell.column_id);
      const existing = get().cells[key];
      if (existing !== undefined && cell.revision < existing.revision) return;
      set({ cells: { ...get().cells, [key]: cell } });
    },

    connect(sheetId, deps = {}) {
      get().disconnect();
      const fetchSnapshot = deps.fetchSnapshot ?? getSnapshot;
      const fetchCellDetail = deps.fetchCellDetail ?? getCellDetail;
      pendingEvents = [];
      snapshotInFlight = 0;
      // Highest revision we already asked the API for, per cell — the
      // detail endpoint returns state at fetch time, which may be newer.
      const requested = new Map<string, number>();
      set({ ...initialState(), sheetId, connection: "connecting" });

      const isCurrent = () => get().sheetId === sheetId && subscription !== null;

      const requestDetail = (event: CellEvent) => {
        const key = cellKey(event.row_id, event.column_id);
        if ((requested.get(key) ?? -1) >= event.revision) return;
        requested.set(key, event.revision);
        void fetchCellDetail(sheetId, event.row_id, event.column_id).then((result) => {
          if (result.kind === "ok" && isCurrent()) get().upsertCell(result.data.cell);
        });
      };

      const refresh = async () => {
        snapshotInFlight += 1;
        try {
          const result = await fetchSnapshot(sheetId);
          if (!isCurrent()) return;
          if (result.kind !== "ok") {
            set({
              connection: "unavailable",
              error:
                result.kind === "api_error"
                  ? result.message
                  : "The sheet snapshot could not be loaded.",
            });
            return;
          }
          const drained = get().loadSnapshot(result.data);
          drained.fetchCells.forEach(requestDetail);
          if (drained.unknownRows) void refresh();
        } finally {
          snapshotInFlight -= 1;
        }
      };

      subscription = subscribeSheetEvents(
        sheetId,
        {
          // open fires on the first connect AND on every reconnect —
          // each one (re)fetches the snapshot: there is no replay, so a
          // drop means possibly-lost events and only a fresh snapshot
          // covers the gap.
          onOpen: () => {
            if (!isCurrent()) return;
            set({ connection: "live" });
            void refresh();
          },
          onEvents: (events) => {
            if (!isCurrent()) return;
            const { fetchCells, unknownRows } = get().applyEvents(events);
            fetchCells.forEach(requestDetail);
            if (unknownRows) void refresh();
          },
          onDrop: () => {
            if (isCurrent()) set({ connection: "reconnecting" });
          },
        },
        deps.createEventSource,
      );
    },

    disconnect() {
      subscription?.close();
      subscription = null;
      pendingEvents = [];
      snapshotInFlight = 0;
      set(initialState());
    },
  };
});

// --- selectors ----------------------------------------------------------------

export const selectCell =
  (rowId: string, columnId: string) =>
  (state: SheetStoreState): SheetCell | undefined =>
    state.cells[cellKey(rowId, columnId)];

export const selectRow =
  (rowId: string) =>
  (state: SheetStoreState): SheetRow | undefined =>
    state.rowsById[rowId];

export const selectColumn =
  (columnId: string) =>
  (state: SheetStoreState): SheetColumn | undefined =>
    state.columnsById[columnId];
