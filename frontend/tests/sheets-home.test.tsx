/**
 * Sheets home tests: the list renders API sheets, each row carries a
 * Delete affordance that asks for confirmation, Confirm delete calls the
 * API for that sheet only and drops the row, Cancel keeps it, and an API
 * failure surfaces the backend message without dropping the row.
 * API modules are module-mocked — no network.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Sheet } from "@/lib/schema/sheet";
import SheetsHome from "@/app/page";

const TS = "2026-10-26T12:00:00Z";

const SHEETS: Sheet[] = [
  { id: "s1", name: "alpha", created_at: TS, updated_at: TS },
  { id: "s2", name: "beta", created_at: TS, updated_at: TS },
];

const api = vi.hoisted(() => ({
  listSheets: vi.fn(),
  createSheet: vi.fn(),
  deleteSheet: vi.fn(),
}));

vi.mock("@/lib/api/sheets", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/sheets")>()),
  listSheets: api.listSheets,
  createSheet: api.createSheet,
  deleteSheet: api.deleteSheet,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function renderReady() {
  api.listSheets.mockResolvedValue({ kind: "ok", data: { sheets: SHEETS } });
  render(<SheetsHome />);
  await screen.findByText("alpha");
}

describe("sheets home", () => {
  it("lists every sheet the API returns", async () => {
    await renderReady();
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("confirm delete calls the API for that sheet only and drops the row", async () => {
    api.deleteSheet.mockResolvedValue({ kind: "ok", data: null });
    const user = userEvent.setup();
    await renderReady();

    await user.click(screen.getByRole("button", { name: "Delete alpha" }));
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));

    expect(api.deleteSheet).toHaveBeenCalledTimes(1);
    expect(api.deleteSheet).toHaveBeenCalledWith("s1");
    await waitFor(() => expect(screen.queryByText("alpha")).not.toBeInTheDocument());
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("cancel keeps the sheet and never calls the API", async () => {
    const user = userEvent.setup();
    await renderReady();

    await user.click(screen.getByRole("button", { name: "Delete alpha" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(api.deleteSheet).not.toHaveBeenCalled();
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete alpha" })).toBeInTheDocument();
  });

  it("an API failure shows the backend message and keeps the row", async () => {
    api.deleteSheet.mockResolvedValue({
      kind: "api_error",
      status: 404,
      code: "not_found",
      message: "sheet not found",
      requestId: "req-1",
    });
    const user = userEvent.setup();
    await renderReady();

    await user.click(screen.getByRole("button", { name: "Delete alpha" }));
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("sheet not found");
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });
});
