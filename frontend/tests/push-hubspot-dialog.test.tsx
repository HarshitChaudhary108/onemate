/**
 * Push-to-HubSpot dialog tests (I4): linked company rows × their done
 * cells become checkboxes; the submit button is disabled only while
 * nothing is checked — after a push (errors included) the dialog must
 * still allow a retry, never locking the user out of the fix-retry loop.
 * API modules are module-mocked — no network; the store is hydrated
 * with a real snapshot.
 */

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PushHubSpotDialog } from "@/components/sheet/push-hubspot-dialog";
import type { SheetCell, SheetSnapshot } from "@/lib/schema/sheet";
import { useSheetStore } from "@/lib/store/sheet-store";

const TS = "2026-10-26T12:00:00Z";
const ROW_ID = "r1";
const COLUMN_ID = "c1";

const LINKS = {
  links: [{ row_id: ROW_ID, remote_id: "4242", object_type: "company" }],
};

const PUSHED = {
  results: [
    {
      row_id: ROW_ID,
      column_id: COLUMN_ID,
      property: "outmate_tech_stack",
      status: "pushed",
      detail: null,
    },
  ],
};

const PUSH_ERROR = {
  results: [
    {
      row_id: ROW_ID,
      column_id: COLUMN_ID,
      property: "outmate_tech_stack",
      status: "error",
      detail: "hubspot_property_missing",
    },
  ],
};

const integrationsApi = vi.hoisted(() => ({
  listHubSpotLinks: vi.fn(),
  pushToHubSpot: vi.fn(),
}));

vi.mock("@/lib/api/integrations", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/integrations")>()),
  ...integrationsApi,
}));

function doneCell(): SheetCell {
  return {
    row_id: ROW_ID,
    column_id: COLUMN_ID,
    status: "done",
    result: { value: "Synthetic stack", status: "provider-reported", sources: [] },
    column_version: 1,
    stale: false,
    revision: 1,
    created_at: TS,
    updated_at: TS,
  };
}

function hydrate() {
  const snapshot: SheetSnapshot = {
    sheet: { id: "s1", name: "targets", created_at: TS, updated_at: TS },
    rows: [
      {
        id: ROW_ID,
        sheet_id: "s1",
        input_name: "Example Robotics",
        input_domain: "example.test",
        position: 0,
        identity_status: "resolved",
        resolved_name: "Example Robotics Ltd",
        resolved_domain: "example.test",
        deleted: false,
        created_at: TS,
        updated_at: TS,
      },
    ],
    columns: [
      {
        id: COLUMN_ID,
        sheet_id: "s1",
        name: "Tech stack",
        prompt_template: "stack of {{Company}}",
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
    cells: [doneCell()],
    latest_run: null,
  };
  act(() => {
    useSheetStore.getState().loadSnapshot(snapshot);
  });
}

async function tickCell(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("checkbox", { name: /push tech stack for example robotics/i }),
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  act(() => {
    useSheetStore.getState().disconnect();
  });
});

describe("PushHubSpotDialog", () => {
  it("lists linked rows' done cells and enables Push only once checked", async () => {
    integrationsApi.listHubSpotLinks.mockResolvedValue({ kind: "ok", data: LINKS });
    const user = userEvent.setup();
    hydrate();
    render(<PushHubSpotDialog sheetId="s1" onClose={() => {}} />);

    const push = screen.getByRole("button", { name: /^push cells$/i });
    expect(push).toBeDisabled();

    await tickCell(user);
    expect(screen.getByRole("button", { name: /^push 1 cell$/i })).toBeEnabled();
  });

  it("clicking anywhere on the cell card toggles it — the select still doesn't", async () => {
    integrationsApi.listHubSpotLinks.mockResolvedValue({ kind: "ok", data: LINKS });
    const user = userEvent.setup();
    hydrate();
    render(<PushHubSpotDialog sheetId="s1" onClose={() => {}} />);

    // Click the card's text, not the tiny checkbox.
    await user.click(await screen.findByText("Synthetic stack"));
    expect(screen.getByRole("button", { name: /^push 1 cell$/i })).toBeEnabled();

    // The property dropdown must not toggle the card.
    await user.click(screen.getByRole("combobox", { name: /hubspot property/i }));
    expect(screen.getByRole("button", { name: /^push 1 cell$/i })).toBeEnabled();
  });

  it("stays clickable after results so an errored push can be retried", async () => {
    integrationsApi.listHubSpotLinks.mockResolvedValue({ kind: "ok", data: LINKS });
    integrationsApi.pushToHubSpot
      .mockResolvedValueOnce({ kind: "ok", data: PUSH_ERROR })
      .mockResolvedValueOnce({ kind: "ok", data: PUSHED });
    const user = userEvent.setup();
    hydrate();
    render(<PushHubSpotDialog sheetId="s1" onClose={() => {}} />);

    await tickCell(user);
    await user.click(screen.getByRole("button", { name: /^push 1 cell$/i }));

    // Error results render AND the button remains usable for a retry.
    expect(await screen.findByText(/hubspot_property_missing/)).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /^push 1 cell$/i });
    expect(retry).toBeEnabled();

    await user.click(retry);
    expect(await screen.findByText("pushed")).toBeInTheDocument();
    expect(integrationsApi.pushToHubSpot).toHaveBeenCalledTimes(2);
    expect(integrationsApi.pushToHubSpot).toHaveBeenLastCalledWith("s1", [
      { row_id: ROW_ID, column_id: COLUMN_ID, property: "outmate_tech_stack" },
    ]);
  });
});
