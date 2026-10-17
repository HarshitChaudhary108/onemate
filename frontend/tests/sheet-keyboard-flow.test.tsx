/**
 * A9 acceptance test: a keyboard-only session can import rows, add a
 * column and inspect a cell. SheetWorkspace is driven by Tab / arrows /
 * Enter / Escape / Ctrl+K / Ctrl+Enter only — no clicks.
 *
 * The sheets API client and the SSE transport are module-mocked (no
 * network); the "server" is a mutable snapshot the mocks read, exactly
 * like a real backend's resnapshot.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SheetEventsHandlers } from "@/lib/api/events";
import type {
  SheetCell,
  SheetColumn,
  SheetRow,
  SheetSnapshot,
} from "@/lib/schema/sheet";
import { useSheetStore } from "@/lib/store/sheet-store";

const TS = "2026-10-17T12:00:00Z";

// --- mocked "server" -----------------------------------------------------------

function makeRow(id: string, name: string | undefined, domain: string | undefined, position: number): SheetRow {
  return {
    id,
    sheet_id: "s1",
    input_name: name ?? null,
    input_domain: domain ?? null,
    position,
    identity_status: "resolved",
    resolved_name: name ?? null,
    resolved_domain: domain ?? null,
    deleted: false,
    created_at: TS,
    updated_at: TS,
  };
}

function makeColumn(id: string, name: string, position: number): SheetColumn {
  return {
    id,
    sheet_id: "s1",
    name,
    prompt_template: `${name} of {{Company}}`,
    output_type: "text",
    strategy: "research",
    source_scope: "web",
    depth: "standard",
    depends_on: [],
    version: 1,
    position,
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

const server: { snapshot: SheetSnapshot } = {
  snapshot: {
    sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
    rows: [],
    columns: [],
    cells: [],
    latest_run: null,
  },
};

const api = vi.hoisted(() => ({
  getSnapshot: vi.fn(),
  getCellDetail: vi.fn(),
  importRows: vi.fn(),
  planColumns: vi.fn(),
  createColumns: vi.fn(),
  createRun: vi.fn(),
  cancelRun: vi.fn(),
}));

vi.mock("@/lib/api/sheets", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/sheets")>();
  return {
    ...mod,
    getSnapshot: api.getSnapshot,
    getCellDetail: api.getCellDetail,
    importRows: api.importRows,
    planColumns: api.planColumns,
    createColumns: api.createColumns,
    createRun: api.createRun,
    cancelRun: api.cancelRun,
  };
});

vi.mock("@/lib/api/events", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/events")>();
  return {
    ...mod,
    // jsdom has no EventSource — an immediately-open no-op subscription;
    // reconnect behavior is covered in sheet-store.test.ts.
    subscribeSheetEvents: (_sheetId: string, handlers: SheetEventsHandlers) => {
      queueMicrotask(() => handlers.onOpen());
      return { close: () => undefined };
    },
  };
});

// --- helpers ------------------------------------------------------------------

/** Tab forward until `target` is focused — proves the element sits in the
 *  real tab order. Fails the test if `max` tabs were not enough. */
async function tabTo(
  user: ReturnType<typeof userEvent.setup>,
  target: Element,
  max = 60,
): Promise<void> {
  for (let i = 0; i < max && document.activeElement !== target; i += 1) {
    await user.tab();
  }
  expect(document.activeElement).toBe(target);
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1200);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);

  server.snapshot = {
    sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
    rows: [],
    columns: [],
    cells: [],
    latest_run: null,
  };

  api.getSnapshot.mockImplementation(async () => ({ kind: "ok", data: server.snapshot }));
  api.getCellDetail.mockImplementation(
    async (_sheetId: string, rowId: string, columnId: string) => ({
      kind: "ok",
      data: {
        cell:
          server.snapshot.cells.find(
            (c) => c.row_id === rowId && c.column_id === columnId,
          ) ?? makeCell(rowId, columnId),
        revisions: [],
      },
    }),
  );
  api.importRows.mockImplementation(
    async (_sheetId: string, rows: { name?: string; domain?: string }[]) => {
      const created = rows.map((row, index) =>
        makeRow(`r${server.snapshot.rows.length + index + 1}`, row.name, row.domain, server.snapshot.rows.length + index),
      );
      server.snapshot = {
        ...server.snapshot,
        rows: [...server.snapshot.rows, ...created],
      };
      return { kind: "ok", data: { rows: created, created: created.length, skipped: 0 } };
    },
  );
  api.planColumns.mockImplementation(async () => ({
    kind: "ok",
    data: {
      intent: "add_columns",
      columns: [
        {
          name: "Industry",
          prompt_template: "Industry of {{Company}}",
          output_type: "text",
          enum_options: [],
          source_scope: "web",
          depth: "standard",
          strategy: "research",
          depends_on: [],
        },
      ],
      estimated_cost_usd: 0.007,
      mock: false,
    },
  }));
  api.createColumns.mockImplementation(
    async (_sheetId: string, specs: { name: string }[]) => {
      const created = specs.map((spec, index) =>
        makeColumn(`c${server.snapshot.columns.length + index + 1}`, spec.name, server.snapshot.columns.length + index),
      );
      const cells = server.snapshot.rows.flatMap((row) =>
        created.map((col) =>
          makeCell(row.id, col.id, {
            status: "done",
            revision: 1,
            rendered_prompt: "Industry of Acme",
            cost: 0.007,
            result: {
              value: "robotics",
              status: "verified",
              sources: [{ provider: "Exa", mock: false, retrieved_at: TS, url: "https://acme.test/" }],
            },
          }),
        ),
      );
      server.snapshot = {
        ...server.snapshot,
        columns: [...server.snapshot.columns, ...created],
        cells: [...server.snapshot.cells, ...cells],
      };
      return { kind: "ok", data: { columns: created, run: null } };
    },
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  act(() => {
    useSheetStore.getState().disconnect();
  });
});

// --- the A9 flow --------------------------------------------------------------

describe("A9 — keyboard-only session", () => {
  it("imports rows, adds a column and inspects a cell without the mouse", async () => {
    const user = userEvent.setup({ delay: 0 });
    const { SheetWorkspace } = await import("@/components/sheet/sheet-workspace");
    render(<SheetWorkspace sheetId="s1" />);

    // Hydrated via the mocked subscription → snapshot.
    await screen.findByTestId("sheet-grid");

    // 1 — IMPORT ROWS: Tab to the toolbar button, Enter opens the dialog.
    const importButton = screen.getByRole("button", { name: /import rows/i });
    await tabTo(user, importButton);
    await user.keyboard("{Enter}");

    // The textarea is autofocused; comma-separated lines need no Tab key.
    const paste = await screen.findByLabelText("Companies to import");
    expect(document.activeElement).toBe(paste);
    await user.type(paste, "Acme,acme.test{Enter}Globex,globex.test");

    await tabTo(user, screen.getByRole("button", { name: /import 2 rows/i }));
    await user.keyboard("{Enter}");
    expect(api.importRows).toHaveBeenCalledWith("s1", [
      { name: "Acme", domain: "acme.test" },
      { name: "Globex", domain: "globex.test" },
    ]);

    await screen.findByText(/Imported 2 rows/);
    await tabTo(user, screen.getByRole("button", { name: "Done" }));
    await user.keyboard("{Enter}");

    // The resnapshot landed both rows.
    await screen.findByText("Acme");
    await screen.findByText("Globex");

    // 2 — ADD A COLUMN: Ctrl+K focuses the prompt bar, Ctrl+Enter plans,
    //     Tab reaches the plan card's "Add only".
    await user.keyboard("{Control>}k{/Control}");
    const promptInput = screen.getByLabelText("Describe the column to add");
    expect(document.activeElement).toBe(promptInput);
    await user.type(promptInput, "industry for each company");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(api.planColumns).toHaveBeenCalledWith("s1", "industry for each company");

    const addOnly = await screen.findByRole("button", { name: "Add only" });
    await tabTo(user, addOnly);
    await user.keyboard("{Enter}");
    expect(api.createColumns).toHaveBeenCalledWith(
      "s1",
      [expect.objectContaining({ name: "Industry" })],
      false,
    );

    // The new column streamed in via the resnapshot.
    await screen.findByRole("columnheader", { name: /industry/i });
    await screen.findAllByText("robotics");

    // 3 — INSPECT A CELL: Tab enters the grid on the pinned company cell
    //     (roving tabindex entry point), ArrowRight reaches the first data
    //     cell, Enter opens the inspector, Escape closes it.
    const companyCell = document.querySelector<HTMLElement>('[data-row="0"][data-col="0"]');
    expect(companyCell).not.toBeNull();
    await tabTo(user, companyCell!);
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(
      document.querySelector('[data-cell-key="r1:c1"]'),
    );
    await user.keyboard("{Enter}");

    expect(api.getCellDetail).toHaveBeenCalledWith("s1", "r1", "c1");
    expect(await screen.findByText("Rendered prompt")).toBeInTheDocument();
    expect(screen.getAllByText("Verified").length).toBeGreaterThan(0);

    await user.keyboard("{Escape}");
    await vi.waitFor(() => {
      expect(screen.queryByText("Rendered prompt")).not.toBeInTheDocument();
    });
  }, 20000);
});
