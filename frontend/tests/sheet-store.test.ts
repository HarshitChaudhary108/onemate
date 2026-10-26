/**
 * Sheet-store tests (P6): normalized merge of the subscribe → snapshot →
 * merge-by-revision protocol. No network — the snapshot/detail fetches and
 * the EventSource are injected fakes.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import type { EventSourceLike } from "@/lib/api/events";
import type { SheetResult } from "@/lib/api/sheets";
import {
  cellKey,
  useSheetStore,
  type SheetConnectDeps,
} from "@/lib/store/sheet-store";
import type { CellDetail, SheetSnapshot } from "@/lib/schema/sheet";

const TS = "2026-10-17T12:00:00Z";

function makeSnapshot(overrides: Partial<SheetSnapshot> = {}): SheetSnapshot {
  return {
    sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
    rows: [
      {
        id: "r1",
        sheet_id: "s1",
        input_name: "Acme",
        input_domain: "acme.test",
        position: 0,
        identity_status: "pending",
        deleted: false,
        created_at: TS,
        updated_at: TS,
      },
      {
        id: "r2",
        sheet_id: "s1",
        input_name: "Globex",
        input_domain: "globex.test",
        position: 1,
        identity_status: "pending",
        deleted: false,
        created_at: TS,
        updated_at: TS,
      },
    ],
    columns: [
      {
        id: "c1",
        sheet_id: "s1",
        name: "Industry",
        prompt_template: "Industry of {{Company}}",
        output_type: "text",
        strategy: "research",
        source_scope: "web",
        depth: "standard",
        depends_on: [],
        version: 1,
        position: 0,
        deleted: false,
        created_at: TS,
        updated_at: TS,
      },
    ],
    cells: [
      {
        row_id: "r1",
        column_id: "c1",
        status: "done",
        result: {
          value: "robotics",
          status: "verified",
          sources: [
            {
              provider: "Exa",
              mock: true,
              retrieved_at: TS,
              url: "https://acme.test/",
            },
          ],
        },
        column_version: 1,
        stale: false,
        revision: 2,
        cost: 0.007,
        created_at: TS,
        updated_at: TS,
      },
      {
        row_id: "r2",
        column_id: "c1",
        status: "idle",
        column_version: 1,
        stale: false,
        revision: 0,
        created_at: TS,
        updated_at: TS,
      },
    ],
    latest_run: null,
    ...overrides,
  };
}

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, ((event: Event) => void)[]>();
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: "open" | "message" | "error", event?: Event): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event ?? ({} as Event));
    }
  }

  frame(events: unknown[]): void {
    this.emit("message", { data: JSON.stringify(events) } as unknown as Event);
  }

  close(): void {
    this.closed = true;
  }
}

function connectDeps(overrides: Partial<SheetConnectDeps> = {}): SheetConnectDeps {
  return {
    fetchSnapshot: vi.fn(async (): Promise<SheetResult<SheetSnapshot>> => ({
      kind: "ok",
      data: makeSnapshot(),
    })),
    fetchCellDetail: vi.fn(
      async (): Promise<SheetResult<CellDetail>> => ({
        kind: "ok",
        data: { cell: makeSnapshot().cells[1], revisions: [] },
      }),
    ),
    createEventSource: (url) => new FakeEventSource(url),
    ...overrides,
  };
}

/** Let queued promise chains (snapshot fetch, detail fetches) resolve. */
async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  FakeEventSource.instances = [];
  useSheetStore.getState().disconnect();
});

describe("loadSnapshot", () => {
  it("normalizes rows, columns and cells", () => {
    const result = useSheetStore.getState().loadSnapshot(makeSnapshot());
    const state = useSheetStore.getState();
    expect(state.hydrated).toBe(true);
    expect(state.rowOrder).toEqual(["r1", "r2"]);
    expect(state.columnOrder).toEqual(["c1"]);
    expect(state.cells[cellKey("r1", "c1")]?.revision).toBe(2);
    expect(result.fetchCells).toEqual([]);
  });

  it("keeps object identity for unchanged cells, rows and columns", () => {
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());
    const before = useSheetStore.getState();
    const cellRef = before.cells[cellKey("r1", "c1")];
    const rowRef = before.rowsById.r1;
    const colRef = before.columnsById.c1;
    const orderRef = before.rowOrder;

    // A resnapshot of identical state must reuse every object — this is
    // what keeps a run-start resnapshot from re-rendering the grid (P7).
    store.loadSnapshot(makeSnapshot());
    const after = useSheetStore.getState();
    expect(after.cells[cellKey("r1", "c1")]).toBe(cellRef);
    expect(after.rowsById.r1).toBe(rowRef);
    expect(after.columnsById.c1).toBe(colRef);
    expect(after.rowOrder).toBe(orderRef);
  });

  it("replaces a cell only when revision or updated_at advanced", () => {
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());
    const before = useSheetStore.getState().cells[cellKey("r1", "c1")];

    // Same revision but a later updated_at: a transient write happened
    // (claim/queue/block) — the newer row wins.
    const changed = makeSnapshot();
    changed.cells[0] = { ...changed.cells[0], status: "running", updated_at: "2026-10-17T12:00:05Z" };
    store.loadSnapshot(changed);
    const running = useSheetStore.getState().cells[cellKey("r1", "c1")];
    expect(running).not.toBe(before);
    expect(running?.status).toBe("running");

    // A resnapshot read that predates the event — earlier updated_at at
    // the same revision — never regresses the cell.
    const stale = makeSnapshot();
    stale.cells[0] = { ...stale.cells[0], status: "done", revision: 2 };
    store.loadSnapshot(stale);
    expect(useSheetStore.getState().cells[cellKey("r1", "c1")]?.status).toBe("running");
  });
});

describe("applyEvents — merge by revision", () => {
  it("applies a higher-revision event and drops a stale one (out-of-order)", () => {
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());

    // done@3 arrives before a stale running@1 — last write wins.
    const accepted = store.applyEvents([
      { type: "cell", row_id: "r1", column_id: "c1", status: "done", revision: 3, stale: false },
    ]);
    expect(accepted.fetchCells).toHaveLength(1);
    expect(useSheetStore.getState().cells[cellKey("r1", "c1")]?.revision).toBe(3);

    const dropped = store.applyEvents([
      { type: "cell", row_id: "r1", column_id: "c1", status: "running", revision: 1, stale: false },
      { type: "cell", row_id: "r1", column_id: "c1", status: "done", revision: 3, stale: false },
    ]);
    const cell = useSheetStore.getState().cells[cellKey("r1", "c1")];
    expect(cell?.status).toBe("done");
    expect(cell?.revision).toBe(3);
    expect(dropped.fetchCells).toEqual([]); // both dropped — not newer
  });

  it("same-revision forward transient events apply — claim never bumps revision", () => {
    // Backend claim_cell / mark_blocked do NOT bump the revision, so the
    // running/blocked transitions always arrive at the cell's current
    // revision. Applying them is the only way "running" / "blocked" can
    // ever render (P7 cell states).
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());
    const out = store.applyEvents([
      { type: "cell", row_id: "r2", column_id: "c1", status: "queued", revision: 0, stale: false },
      { type: "cell", row_id: "r2", column_id: "c1", status: "running", revision: 0, stale: false },
    ]);
    expect(out.fetchCells).toEqual([]);
    expect(useSheetStore.getState().cells[cellKey("r2", "c1")]?.status).toBe("running");
  });

  it("same-revision transient rewinds and terminal duplicates are dropped", () => {
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());
    store.applyEvents([
      { type: "cell", row_id: "r2", column_id: "c1", status: "running", revision: 0, stale: false },
    ]);
    // A delayed queued@0 can never legitimately follow running@0 — dropped.
    store.applyEvents([
      { type: "cell", row_id: "r2", column_id: "c1", status: "queued", revision: 0, stale: false },
    ]);
    expect(useSheetStore.getState().cells[cellKey("r2", "c1")]?.status).toBe("running");
    // r1c1 is already done@2 — a same-revision terminal event is a
    // duplicate, never a new write, so it is dropped without a fetch.
    const out = store.applyEvents([
      { type: "cell", row_id: "r1", column_id: "c1", status: "done", revision: 2, stale: false },
    ]);
    expect(out.fetchCells).toEqual([]);
    expect(useSheetStore.getState().cells[cellKey("r1", "c1")]?.status).toBe("done");
  });

  it("an event on an unknown cell requests a detail fetch", () => {
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());
    const out = store.applyEvents([
      { type: "cell", row_id: "r9", column_id: "c9", status: "queued", revision: 0, stale: false },
    ]);
    expect(out.fetchCells).toHaveLength(1);
  });

  it("row events merge identity fields; unknown rows flag a resnapshot", () => {
    const store = useSheetStore.getState();
    store.loadSnapshot(makeSnapshot());
    const out = store.applyEvents([
      {
        type: "row",
        row_id: "r1",
        identity_status: "resolved",
        resolved_name: "Acme Corp",
        resolved_domain: "acme.test",
      },
      {
        type: "row",
        row_id: "ghost",
        identity_status: "resolved",
        resolved_name: null,
        resolved_domain: null,
      },
      { type: "run", run_id: "run1", status: "started", reason: null },
    ]);
    expect(out.unknownRows).toBe(true);
    expect(useSheetStore.getState().rowsById.r1.identity_status).toBe("resolved");
    expect(useSheetStore.getState().runStates.run1?.status).toBe("started");
  });
});

describe("connect — subscribe, snapshot, merge, reconnect", () => {
  it("fetches the snapshot on open, buffers events until it lands", async () => {
    let resolveSnapshot: ((r: SheetResult<SheetSnapshot>) => void) | undefined;
    const deps = connectDeps({
      fetchSnapshot: vi.fn(
        () =>
          new Promise<SheetResult<SheetSnapshot>>((resolve) => {
            resolveSnapshot = resolve;
          }),
      ),
    });
    useSheetStore.getState().connect("s1", deps);
    const es = FakeEventSource.instances[0];
    expect(es.url).toContain("/api/sheets/s1/events");

    es.emit("open");
    // Event arrives while the snapshot fetch is still in flight → buffered.
    es.frame([
      { type: "cell", row_id: "r1", column_id: "c1", status: "done", revision: 4, stale: false },
    ]);
    expect(useSheetStore.getState().hydrated).toBe(false);

    resolveSnapshot!({ kind: "ok", data: makeSnapshot() });
    await flush();

    const cell = useSheetStore.getState().cells[cellKey("r1", "c1")];
    // Buffered event merged on top of the snapshot — revision 4 wins.
    expect(cell?.status).toBe("done");
    expect(cell?.revision).toBe(4);
    // Terminal event → cell detail requested and merged via upsertCell.
    await vi.waitFor(() => expect(deps.fetchCellDetail).toHaveBeenCalled());
  });

  it("re-snapshots on reconnect and never regresses a newer cell", async () => {
    const deps = connectDeps();
    useSheetStore.getState().connect("s1", deps);
    const es = FakeEventSource.instances[0];
    es.emit("open");
    await vi.waitFor(() => expect(useSheetStore.getState().hydrated).toBe(true));

    // A done@5 cell event lands, then the connection drops.
    es.frame([
      { type: "cell", row_id: "r1", column_id: "c1", status: "done", revision: 5, stale: false },
    ]);
    expect(useSheetStore.getState().cells[cellKey("r1", "c1")]?.revision).toBe(5);

    es.emit("error");
    expect(useSheetStore.getState().connection).toBe("reconnecting");

    // Reconnect: the re-snapshot still reports revision 2 (its read raced
    // the write) — the store must NOT regress the merged cell.
    es.emit("open");
    await vi.waitFor(() =>
      expect(useSheetStore.getState().connection).toBe("live"),
    );
    expect(deps.fetchSnapshot).toHaveBeenCalledTimes(2);
    const cell = useSheetStore.getState().cells[cellKey("r1", "c1")];
    expect(cell?.revision).toBe(5);
    expect(cell?.status).toBe("done");
  });

  it("an unknown row event triggers a re-snapshot", async () => {
    const deps = connectDeps();
    useSheetStore.getState().connect("s1", deps);
    const es = FakeEventSource.instances[0];
    es.emit("open");
    await vi.waitFor(() => expect(useSheetStore.getState().hydrated).toBe(true));

    es.frame([
      {
        type: "row",
        row_id: "new-row",
        identity_status: "pending",
        resolved_name: null,
        resolved_domain: null,
      },
    ]);
    await vi.waitFor(() =>
      expect(vi.mocked(deps.fetchSnapshot!).mock.calls.length).toBeGreaterThan(1),
    );
  });

  it("a run lifecycle event triggers a resnapshot", async () => {
    // queue_cells / cancel / stop only touch the DB — the only truthful
    // source for queued statuses and the run denominator is a fresh read.
    const deps = connectDeps();
    useSheetStore.getState().connect("s1", deps);
    const es = FakeEventSource.instances[0];
    es.emit("open");
    await vi.waitFor(() => expect(useSheetStore.getState().hydrated).toBe(true));
    expect(deps.fetchSnapshot).toHaveBeenCalledTimes(1);

    es.frame([{ type: "run", run_id: "run1", status: "started", reason: null }]);
    await vi.waitFor(() => expect(deps.fetchSnapshot).toHaveBeenCalledTimes(2));
    expect(useSheetStore.getState().runStates.run1?.status).toBe("started");
  });

  it("a terminal cell event triggers exactly one detail fetch per revision", async () => {
    const detailCell = {
      ...makeSnapshot().cells[1],
      status: "done" as const,
      revision: 1,
      // The finish write bumps updated_at — a detail read for rev 1 always
      // carries a later timestamp than the rev-0 idle row.
      updated_at: "2026-10-17T12:00:01Z",
      result: { value: "synthetic", status: "unavailable" as const },
    };
    const deps = connectDeps({
      fetchCellDetail: vi.fn(async (): Promise<SheetResult<CellDetail>> => ({
        kind: "ok",
        data: { cell: detailCell, revisions: [] },
      })),
    });
    useSheetStore.getState().connect("s1", deps);
    const es = FakeEventSource.instances[0];
    es.emit("open");
    await vi.waitFor(() => expect(useSheetStore.getState().hydrated).toBe(true));

    // Duplicate delivery of the same revision → one fetch, not two.
    es.frame([
      { type: "cell", row_id: "r2", column_id: "c1", status: "done", revision: 1, stale: false },
      { type: "cell", row_id: "r2", column_id: "c1", status: "done", revision: 1, stale: false },
    ]);
    await vi.waitFor(() => expect(deps.fetchCellDetail).toHaveBeenCalledTimes(1));
    await flush();
    const cell = useSheetStore.getState().cells[cellKey("r2", "c1")];
    expect(cell?.status).toBe("done");
    expect(cell?.result?.value).toBe("synthetic");
  });
});
