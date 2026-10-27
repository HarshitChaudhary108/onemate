/**
 * Integrations page tests (I4): disconnected renders the Connect card,
 * connected renders real /status counts, `?hubspot_error=` surfaces the
 * one-line outcome banner, and the import flow calls the API with the
 * selected sheet + checked remote ids. The API modules are module-mocked
 * — no network; `next/navigation` is mocked for `useSearchParams`.
 */

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Sheet } from "@/lib/schema/sheet";
import IntegrationsPage from "@/app/integrations/page";

const TS = "2026-10-26T12:00:00Z";

const CONNECTED = {
  connected: true,
  portal_id: "12345678",
  scopes: [
    "crm.objects.companies.read",
    "crm.objects.companies.write",
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
  ],
  connected_at: TS,
  last_sync_at: null,
  companies_imported: 3,
  contacts_imported: 1,
  mock: false,
};

const DISCONNECTED = {
  connected: false,
  portal_id: null,
  scopes: [],
  connected_at: null,
  last_sync_at: null,
  companies_imported: 0,
  contacts_imported: 0,
  mock: false,
};

const SHEET: Sheet = { id: "s1", name: "targets", created_at: TS, updated_at: TS };

const nav = vi.hoisted(() => ({ search: "" }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const integrationsApi = vi.hoisted(() => ({
  hubspotStatus: vi.fn(),
  listHubSpotObjects: vi.fn(),
  listHubSpotLinks: vi.fn(),
  importFromHubSpot: vi.fn(),
  pushToHubSpot: vi.fn(),
  refreshHubSpot: vi.fn(),
  disconnectHubSpot: vi.fn(),
  connectHubSpotUrl: vi.fn(() => "http://api.test:8000/api/integrations/hubspot/connect"),
}));

vi.mock("@/lib/api/integrations", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/integrations")>()),
  ...integrationsApi,
}));

const sheetsApi = vi.hoisted(() => ({ listSheets: vi.fn() }));

vi.mock("@/lib/api/sheets", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/sheets")>()),
  listSheets: sheetsApi.listSheets,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  nav.search = "";
});

describe("IntegrationsPage", () => {
  it("disconnected renders a single Connect card — no invented counts", async () => {
    integrationsApi.hubspotStatus.mockResolvedValue({ kind: "ok", data: DISCONNECTED });
    render(<IntegrationsPage />);

    const connect = await screen.findByRole("link", { name: /connect hubspot/i });
    expect(connect).toHaveAttribute(
      "href",
      "http://api.test:8000/api/integrations/hubspot/connect",
    );
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.queryByText("Companies imported")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /import from hubspot/i }),
    ).not.toBeInTheDocument();
  });

  it("connected renders the real status counts, portal id, and scopes", async () => {
    integrationsApi.hubspotStatus.mockResolvedValue({ kind: "ok", data: CONNECTED });
    render(<IntegrationsPage />);

    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("portal 12345678")).toBeInTheDocument();
    expect(
      screen.getByText("Companies imported").parentElement as HTMLElement,
    ).toHaveTextContent("3");
    expect(
      screen.getByText("Contacts imported").parentElement as HTMLElement,
    ).toHaveTextContent("1");
    expect(screen.getByText("Last sync").parentElement as HTMLElement).toHaveTextContent(
      "Never",
    );
    expect(screen.getByText(/crm\.objects\.companies\.read/)).toBeInTheDocument();
  });

  it("?hubspot_error= surfaces the one-line outcome banner", async () => {
    nav.search = "hubspot_error=oauth_state_mismatch";
    integrationsApi.hubspotStatus.mockResolvedValue({ kind: "ok", data: DISCONNECTED });
    render(<IntegrationsPage />);

    const banner = await screen.findByTestId("hubspot-outcome");
    expect(banner).toHaveTextContent(/sign-in session expired/i);
    expect(banner).toHaveAttribute("role", "alert");
  });

  it("?hubspot=connected surfaces the success banner", async () => {
    nav.search = "hubspot=connected";
    integrationsApi.hubspotStatus.mockResolvedValue({ kind: "ok", data: CONNECTED });
    render(<IntegrationsPage />);

    const banner = await screen.findByTestId("hubspot-outcome");
    expect(banner).toHaveTextContent("HubSpot connected.");
  });

  it("import flow posts the selected sheet + checked ids", async () => {
    const user = userEvent.setup();
    integrationsApi.hubspotStatus.mockResolvedValue({ kind: "ok", data: CONNECTED });
    sheetsApi.listSheets.mockResolvedValue({ kind: "ok", data: { sheets: [SHEET] } });
    integrationsApi.listHubSpotObjects.mockImplementation(async (objectType: string) =>
      objectType === "companies"
        ? {
            kind: "ok" as const,
            data: [
              {
                remote_id: "1001",
                object_type: "company",
                properties: {
                  name: "Example Robotics Ltd",
                  domain: "example-robotics.test",
                },
              },
            ],
          }
        : { kind: "ok" as const, data: [] },
    );
    integrationsApi.importFromHubSpot.mockResolvedValue({
      kind: "ok",
      data: { created: 1, skipped: 0, linked_companies: 1, linked_contacts: 0 },
    });

    render(<IntegrationsPage />);
    await user.click(
      await screen.findByRole("button", { name: /import from hubspot/i }),
    );

    const panel = await screen.findByTestId("import-panel");
    await user.selectOptions(await within(panel).findByLabelText("Target sheet"), "s1");
    await user.click(
      await within(panel).findByRole("checkbox", { name: /Example Robotics Ltd/ }),
    );
    await user.click(within(panel).getByRole("button", { name: /import selected/i }));

    expect(integrationsApi.importFromHubSpot).toHaveBeenCalledWith("s1", {
      company_ids: ["1001"],
      contact_ids: [],
    });
    const summary = await within(panel).findByRole("status");
    expect(summary).toHaveTextContent(/Imported 1 row/);
    expect(summary).toHaveTextContent(/1 company linked/);
    // Status refetch keeps the card counts honest after the import.
    expect(integrationsApi.hubspotStatus.mock.calls.length).toBeGreaterThan(1);
  });
});
