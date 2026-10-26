/**
 * Grid component tests (P7): every cell state, keyboard navigation, and the
 * gate invariant — a cell event or a new-column resnapshot must never
 * re-render existing cells (asserted with a render-counting CellView spy).
 * No network: the store is hydrated directly from synthetic snapshots.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CellView } from "@/components/sheet/cell-view";
import { SheetGrid } from "@/components/sheet/sheet-grid";
import { SheetToolbar } from "@/components/sheet/sheet-toolbar";
import type { SheetCell, SheetColumn, SheetRow, SheetSnapshot } from "@/lib/schema/sheet";
import { cellKey, useSheetStore } from "@/lib/store/sheet-store";

const TS = "2026-10-17T12:00:00Z";

// --- render counting ---------------------------------------------------------
// The gate needs render evidence per cell. A mock on the exported CellView
// would not see renders — SheetCellView references it through the module's
// own bindings, not the export — so SheetCellView is replaced by a counting
// twin with IDENTICAL subscriptions (cell + column output type), rendering
// the real CellView.

const renderCounts = vi.hoisted(() => new Map<string, number>());

vi.mock("@/components/sheet/cell-view", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/components/sheet/cell-view")>();
  const { memo } = await import("react");
  const { useSheetStore, cellKey } = await import("@/lib/store/sheet-store");
  const CountingSheetCellView = memo(function CountingSheetCellView({
    rowId,
    columnId,
    onRetry,
  }: {
    rowId: string;
    columnId: string;
    onRetry?: () => void;
  }) {
    renderCounts.set(cellKey(rowId, columnId), (renderCounts.get(cellKey(rowId, columnId)) ?? 0) + 1);
    const cell = useSheetStore((state) => state.cells[cellKey(rowId, columnId)]);
    const outputType = useSheetStore(
      (state) => state.columnsById[columnId]?.output_type ?? "text",
    );
    return <mod.CellView cell={cell} outputType={outputType} onRetry={onRetry} />;
  });
  return { ...mod, SheetCellView: CountingSheetCellView };
});

// --- fixtures ---------------------------------------------------------------

function makeRow(index: number): SheetRow {
  return {
    id: `r${index}`,
    sheet_id: "s1",
    input_name: `Company ${index}`,
    input_domain: `c${index}.test`,
    position: index,
    identity_status: index % 3 === 0 ? "resolved" : "pending",
    resolved_name: index % 3 === 0 ? `Company ${index} Ltd` : null,
    resolved_domain: index % 3 === 0 ? `c${index}.test` : null,
    deleted: false,
    created_at: TS,
    updated_at: TS,
  };
}

function makeColumn(index: number): SheetColumn {
  return {
    id: `c${index}`,
    sheet_id: "s1",
    name: `Column ${index}`,
    prompt_template: `Prompt ${index} for {{Company}}`,
    output_type: "text",
    strategy: "research",
    source_scope: "web",
    depth: "standard",
    depends_on: [],
    version: 1,
    position: index,
    deleted: false,
    created_at: TS,
    updated_at: TS,
  };
}

function makeCell(rowId: string, columnId: string, overrides: Partial<SheetCell> = {}): SheetCell {
  return {
    row_id: rowId,
    column_id: columnId,
    status: "idle",
    column_version: 1,
    stale: false,
    revision: 0,
    created_at: TS,
    updated_at: TS,
    ...overrides,
  };
}

function makeSnapshot(rowCount: number, colCount: number): SheetSnapshot {
  const rows = Array.from({ length: rowCount }, (_, i) => makeRow(i));
  const columns = Array.from({ length: colCount }, (_, i) => makeColumn(i));
  const cells = rows.flatMap((row) => columns.map((col) => makeCell(row.id, col.id)));
  return {
    sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
    rows,
    columns,
    cells,
    latest_run: null,
  };
}

function hydrate(snapshot: SheetSnapshot) {
  act(() => {
    useSheetStore.getState().loadSnapshot(snapshot);
  });
}

beforeEach(() => {
  renderCounts.clear();
  // virtual-core measures the scroll element via clientWidth/clientHeight —
  // always 0 in jsdom — so stub a 1200×600 viewport (~16 rows + overscan).
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1200);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  act(() => {
    useSheetStore.getState().disconnect();
  });
});

// --- cell states ---------------------------------------------------------------

describe("CellView states — always icon + text", () => {
  const base = makeCell("r1", "c1");

  it("idle shows a muted dash and names the state", () => {
    render(<CellView cell={{ ...base, status: "idle" }} outputType="text" />);
    expect(screen.getByText("Not run")).toBeInTheDocument();
  });

  it("queued shows a muted skeleton and names the state", () => {
    render(<CellView cell={{ ...base, status: "queued" }} outputType="text" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("running shows a spinner and the word Running", () => {
    render(<CellView cell={{ ...base, status: "running" }} outputType="text" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("done shows the value with a status mark", () => {
    render(
      <CellView
        cell={{
          ...base,
          status: "done",
          revision: 1,
          result: {
            value: "robotics",
            status: "verified",
            sources: [{ provider: "Exa", mock: false, retrieved_at: TS, url: "https://x.test" }],
          },
        }}
        outputType="text"
      />,
    );
    expect(screen.getByText("robotics")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("done without a value shows Unavailable — never a fabricated value", () => {
    render(
      <CellView
        cell={{ ...base, status: "done", revision: 1, result: { status: "unavailable" } }}
        outputType="text"
      />,
    );
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });

  it("error shows the message and a retry control", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <CellView cell={{ ...base, status: "error", error: "exa timeout" }} outputType="text" onRetry={onRetry} />,
    );
    expect(screen.getByText("exa timeout")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("blocked names the upstream column from the cell error", () => {
    render(
      <CellView
        cell={{ ...base, status: "blocked", error: "upstream column 'Industry' is unavailable" }}
        outputType="text"
      />,
    );
    expect(screen.getByText(/upstream column 'Industry'/)).toBeInTheDocument();
  });

  it("a stale done cell shows the amber dot marker", () => {
    render(
      <CellView
        cell={{
          ...base,
          status: "done",
          stale: true,
          revision: 1,
          result: { value: "old", status: "inferred" },
        }}
        outputType="text"
      />,
    );
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("old")).toBeInTheDocument();
  });
});

// --- keyboard navigation ---------------------------------------------------------

describe("SheetGrid keyboard navigation", () => {
  it("arrows move the active cell, Enter inspects, Escape closes", async () => {
    const user = userEvent.setup();
    const onInspect = vi.fn();
    const onClose = vi.fn();
    hydrate(makeSnapshot(3, 2));
    render(<SheetGrid onInspectCell={onInspect} onCloseInspector={onClose} />);

    const first = document.querySelector<HTMLElement>('[data-cell-key="r0:c0"]');
    expect(first).not.toBeNull();
    first!.focus();

    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(
      document.querySelector('[data-cell-key="r0:c1"]'),
    );
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(
      document.querySelector('[data-cell-key="r1:c1"]'),
    );
    await user.keyboard("{Enter}");
    expect(onInspect).toHaveBeenCalledWith("r1", "c1");
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("arrow-left reaches the pinned company column and Enter profiles the row", async () => {
    const user = userEvent.setup();
    const onProfile = vi.fn();
    const onInspect = vi.fn();
    hydrate(makeSnapshot(2, 1));
    render(<SheetGrid onInspectCell={onInspect} onDeepProfile={onProfile} />);

    document.querySelector<HTMLElement>('[data-cell-key="r0:c0"]')!.focus();
    await user.keyboard("{ArrowLeft}");
    const company = document.querySelector<HTMLElement>('[data-row="0"][data-col="0"]');
    expect(document.activeElement).toBe(company);
    await user.keyboard("{Enter}");
    expect(onProfile).toHaveBeenCalledWith("r0");
    expect(onInspect).not.toHaveBeenCalled();
  });
});

// --- render isolation (the P7 gate) ---------------------------------------------

describe("P7 gate — a cell event or a new column never re-renders the grid", () => {
  it("200×30: one cell event re-renders exactly one cell", () => {
    hydrate(makeSnapshot(200, 30));
    render(<SheetGrid onInspectCell={vi.fn()} />);
    const before = new Map(renderCounts);
    expect(before.get(cellKey("r0", "c0"))).toBe(1);

    act(() => {
      useSheetStore.getState().applyEvents([
        { type: "cell", row_id: "r0", column_id: "c0", status: "running", revision: 0, stale: false },
      ]);
    });

    for (const [key, count] of renderCounts) {
      if (key === cellKey("r0", "c0")) expect(count).toBe(2);
      else expect(count).toBe(before.get(key));
    }
  });

  it("a new-column resnapshot adds cells without touching existing ones", () => {
    const snapshot = makeSnapshot(200, 30);
    hydrate(snapshot);
    render(<SheetGrid onInspectCell={vi.fn()} />);

    const existingNode = document.querySelector('[data-cell-key="r0:c0"]');
    const before = new Map(renderCounts);

    // The backend appends column 31; its cells arrive via the resnapshot.
    const next = makeSnapshot(200, 31);
    act(() => {
      useSheetStore.getState().loadSnapshot(next);
    });

    // No existing cell re-rendered; the same DOM nodes are still mounted
    // (no layout shift, no remount).
    for (const [key, count] of renderCounts) {
      const [, colId] = key.split(":");
      if (colId === "c30") continue; // the new column mounts fresh
      expect(count, `cell ${key} re-rendered`).toBe(before.get(key));
    }
    expect(document.querySelector('[data-cell-key="r0:c0"]')).toBe(existingNode);
    expect(document.querySelector('[data-cell-key="r0:c30"]')).not.toBeNull();
    expect(screen.getByRole("columnheader", { name: /column 30/i })).toBeInTheDocument();
  });
});

// --- overflow containment --------------------------------------------------------
// jsdom cannot measure layout overflow, so these assert the containment
// contract: the fixed-width gridcell clips, the value text is a shrinking
// flex item that ellipsizes, the full value stays reachable via title,
// and cell-state controls still render.

describe("cell overflow containment", () => {
  const LONG_VALUE = "unbrokenvaluewithnospacesatall".repeat(10);

  function hydrateLongValue() {
    const snapshot = makeSnapshot(2, 1);
    snapshot.cells[0] = {
      ...snapshot.cells[0],
      status: "done",
      revision: 1,
      result: { value: LONG_VALUE, status: "verified" },
    };
    hydrate(snapshot);
  }

  it("a long unbroken value is inside a clipped cell on a shrinking, truncating text node", () => {
    hydrateLongValue();
    render(<SheetGrid onInspectCell={vi.fn()} />);

    const gridcell = document.querySelector<HTMLElement>('[data-cell-key="r0:c0"]');
    expect(gridcell).not.toBeNull();
    expect(gridcell!.className).toContain("overflow-hidden");
    expect(gridcell!.className).toContain("w-[220px]");

    const text = screen.getByText(LONG_VALUE);
    expect(text.className).toContain("truncate");
    expect(text.className).toContain("min-w-0");
    expect(text.closest('[data-state="done"]')!.className).toContain("min-w-0");
    // The complete value remains accessible via the existing tooltip.
    expect(text).toHaveAttribute("title", LONG_VALUE);
  });

  it("a done cell keeps its status mark next to a truncated long value", () => {
    hydrateLongValue();
    render(<SheetGrid onInspectCell={vi.fn()} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("an error cell keeps its retry control clickable alongside a long message", async () => {
    const user = userEvent.setup();
    const onRetryErrors = vi.fn();
    const snapshot = makeSnapshot(2, 1);
    snapshot.cells[0] = {
      ...snapshot.cells[0],
      status: "error",
      error: "provider timed out after a very long explanation ".repeat(5),
    };
    hydrate(snapshot);
    render(<SheetGrid onInspectCell={vi.fn()} onRetryErrors={onRetryErrors} />);

    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetryErrors).toHaveBeenCalledTimes(1);
  });
});

// --- toolbar true counts ---------------------------------------------------------

describe("SheetToolbar run progress", () => {
  it("shows true counts from provider-run cells only", () => {
    const snapshot = makeSnapshot(2, 2);
    snapshot.latest_run = {
      id: "run1",
      sheet_id: "s1",
      cap: 1,
      spent: 0.007,
      cancelled: false,
      created_at: TS,
    };
    // Three of four cells belong to the run; two finished, one has cost.
    snapshot.cells[0] = { ...snapshot.cells[0], status: "done", revision: 1, run_id: "run1", cost: 0.007, result: { value: "x", status: "inferred" } };
    snapshot.cells[1] = { ...snapshot.cells[1], status: "done", revision: 1, run_id: "run1", result: { value: "y", status: "inferred" } };
    snapshot.cells[2] = { ...snapshot.cells[2], status: "running", run_id: "run1" };
    hydrate(snapshot);
    act(() => {
      useSheetStore.getState().applyEvents([
        { type: "run", run_id: "run1", status: "started", reason: null },
      ]);
    });
    render(
      <SheetToolbar
        onCancelRun={vi.fn()}
        onImportRows={vi.fn()}
        onFindCompanies={vi.fn()}
      />,
    );
    expect(screen.getByTestId("run-progress")).toHaveTextContent("2 of 3 cells");
    expect(screen.getByText(/Spent \$0\.0070/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel run/i })).toBeInTheDocument();
    expect(screen.getByText(/2 rows · 2 columns/)).toBeInTheDocument();
  });
});
