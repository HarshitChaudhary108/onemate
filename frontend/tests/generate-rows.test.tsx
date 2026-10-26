/**
 * Generate-rows dialog tests (P9a): the estimate is labelled an estimate,
 * the form submits by keyboard (Enter), the summary reports the true
 * created/skipped counts plus provider-reported cost only, and a success
 * triggers the workspace resnapshot. The sheets client is module-mocked —
 * no network.
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SheetResult } from "@/lib/api/sheets";
import type { GenerateRowsResponse, SheetRow, SheetSnapshot } from "@/lib/schema/sheet";
import { useSheetStore } from "@/lib/store/sheet-store";
import { GenerateRowsDialog } from "@/components/sheet/generate-rows-dialog";

const TS = "2026-10-17T12:00:00Z";

const GENERATED_ROW: SheetRow = {
  id: "r1",
  sheet_id: "s1",
  input_name: null,
  input_domain: "robotics-one.test",
  position: 0,
  identity_status: "pending",
  resolved_name: null,
  resolved_domain: null,
  deleted: false,
  created_at: TS,
  updated_at: TS,
};

function ok(data: GenerateRowsResponse): SheetResult<GenerateRowsResponse> {
  return { kind: "ok", data };
}

const api = vi.hoisted(() => ({
  generateRows: vi.fn(),
  getSnapshot: vi.fn(),
}));

vi.mock("@/lib/api/sheets", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/sheets")>();
  return { ...mod, generateRows: api.generateRows, getSnapshot: api.getSnapshot };
});

vi.mock("@/lib/api/events", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/events")>();
  return {
    ...mod,
    // jsdom has no EventSource — an immediately-open no-op subscription.
    subscribeSheetEvents: (_sheetId: string, handlers: { onOpen: () => void }) => {
      queueMicrotask(() => handlers.onOpen());
      return { close: () => undefined };
    },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useSheetStore.getState().disconnect();
});

describe("GenerateRowsDialog", () => {
  it("labels the cost as an estimate and disables submit on an empty query", () => {
    render(
      <GenerateRowsDialog sheetId="s1" onClose={vi.fn()} onGenerated={vi.fn()} />,
    );
    expect(screen.getByText(/estimate only/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Find companies" })).toBeDisabled();
  });

  it("submits via keyboard (Enter) and reports true counts + provider cost", async () => {
    const user = userEvent.setup();
    api.generateRows.mockResolvedValue(
      ok({
        rows: [GENERATED_ROW],
        created: 1,
        skipped: 2,
        estimated_cost_usd: 0.015,
        cost_usd: 0.007,
        mock: true,
      }),
    );
    const onGenerated = vi.fn();
    render(
      <GenerateRowsDialog sheetId="s1" onClose={vi.fn()} onGenerated={onGenerated} />,
    );

    const input = screen.getByLabelText("Companies to find");
    await user.selectOptions(screen.getByLabelText("Max results"), "3");
    await user.type(input, "robotics suppliers in Germany");
    await user.keyboard("{Enter}");

    expect(api.generateRows).toHaveBeenCalledWith("s1", {
      query: "robotics suppliers in Germany",
      max_results: 3,
    });
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/Added 1 row/);
    expect(status).toHaveTextContent(/2 skipped/);
    expect(status).toHaveTextContent(/provider-reported cost \$0\.0070/);
    expect(status).toHaveTextContent(/Mock provider/);
    expect(onGenerated).toHaveBeenCalledTimes(1);
  });

  it("shows the backend error verbatim and never resnapshots", async () => {
    const user = userEvent.setup();
    api.generateRows.mockResolvedValue({
      kind: "api_error",
      status: 502,
      code: "exa_credits_exhausted",
      message: "credits exhausted",
      requestId: "req-1",
    });
    const onGenerated = vi.fn();
    render(
      <GenerateRowsDialog sheetId="s1" onClose={vi.fn()} onGenerated={onGenerated} />,
    );
    await user.type(screen.getByLabelText("Companies to find"), "anything");
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("credits exhausted");
    expect(onGenerated).not.toHaveBeenCalled();
  });
});

describe("workspace wiring", () => {
  it("a successful generation resnapshots the store", async () => {
    const snapshot: SheetSnapshot = {
      sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
      rows: [],
      columns: [],
      cells: [],
      latest_run: null,
    };
    api.getSnapshot.mockResolvedValue({ kind: "ok", data: snapshot });
    api.generateRows.mockResolvedValue(
      ok({
        rows: [GENERATED_ROW],
        created: 1,
        skipped: 0,
        estimated_cost_usd: 0.015,
        cost_usd: null,
        mock: false,
      }),
    );

    const user = userEvent.setup();
    const { SheetWorkspace } = await import("@/components/sheet/sheet-workspace");
    render(<SheetWorkspace sheetId="s1" />);
    await screen.findByTestId("sheet-toolbar");
    await vi.waitFor(() => {
      expect(api.getSnapshot).toHaveBeenCalled();
    });
    const callsBefore = api.getSnapshot.mock.calls.length;

    await user.click(screen.getByRole("button", { name: /find companies/i }));
    await user.type(
      await screen.findByLabelText("Companies to find"),
      "robotics suppliers",
    );
    await user.keyboard("{Enter}");
    await screen.findByRole("status");

    // The resnapshot fetched the sheet again after generation.
    await vi.waitFor(() => {
      expect(api.getSnapshot.mock.calls.length).toBeGreaterThan(callsBefore);
    });
  });
});
